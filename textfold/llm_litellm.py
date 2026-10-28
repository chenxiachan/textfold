"""A model reached through the LiteLLM SDK (`pip install 'textfold[litellm]'`): Bedrock, Vertex AI, Azure, Gemini, Mistral,
Cohere and the many other providers it names as "provider/model" (anthropic/claude-sonnet-4-6,
bedrock/anthropic.claude-sonnet-4-6, vertex_ai/gemini-2.5-flash, ...), including those that are not OpenAI-compatible.

Each provider's own credentials are read where it keeps them (ANTHROPIC_API_KEY, the AWS and Google application
credentials, AZURE_API_KEY, ...), so none is stored here. The request is the one llm_openai.py sends: the schema rides in
the prompt and JSON mode is asked for, and a provider that refuses JSON mode is asked again without it. A parameter a
provider does not take (JSON mode, a reasoning level) is dropped by LiteLLM rather than refused. The cost of a reply comes
from LiteLLM's price list, for any provider it knows.
"""
import json
import re

WAITS = 3   # LiteLLM's own retries on a rate limit or an overloaded provider


def call(prompt, schema, spec, system, timeout):
    try:
        import litellm
    except ImportError:
        raise RuntimeError("LiteLLM models need the SDK: pip install 'textfold[litellm]'")
    model = spec["model"]
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": f"{prompt}\n\nReply with one JSON object that matches this JSON Schema:\n{json.dumps(schema)}"},
        ],
        "timeout": timeout,
        "num_retries": WAITS,
        "drop_params": True,
    }
    if spec.get("base"):   # a self-hosted endpoint (ollama/, hosted_vllm/, an Azure deployment); else the provider's own
        body["api_base"] = spec["base"]
    if spec.get("effort") in ("low", "medium", "high"):   # LiteLLM spells it for each provider; off is the model's own way
        body["reasoning_effort"] = spec["effort"]
    reply = None
    for json_mode in (True, False):
        try:
            reply = litellm.completion(**body, **({"response_format": {"type": "json_object"}} if json_mode else {}))
            break
        except litellm.exceptions.BadRequestError as e:   # JSON mode refused as spelled: once more without it
            if not json_mode:
                raise RuntimeError(f"LiteLLM ({model}): {_said(e)}")
        except litellm.exceptions.AuthenticationError:
            raise RuntimeError(f"LiteLLM ({model}): the provider rejected the credentials")
        except litellm.exceptions.NotFoundError:
            raise RuntimeError(f"LiteLLM: model {model} not found")
        except litellm.exceptions.RateLimitError:
            raise RuntimeError(f"LiteLLM ({model}): rate limited, try again in a minute")
        except litellm.exceptions.APIConnectionError as e:
            raise RuntimeError(f"LiteLLM ({model}): could not connect: {_said(e)}")
        except litellm.exceptions.APIError as e:
            raise RuntimeError(f"LiteLLM ({model}): {_said(e)}")
        except Exception as e:   # a provider's own error (Vertex AI's, Bedrock's) that LiteLLM gives back unmapped
            raise RuntimeError(f"LiteLLM ({model}): {_said(e)}") from e
    text = (reply.choices[0].message.content or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise RuntimeError(f"LiteLLM ({model}): no JSON in reply")
    return json.loads(m.group(0)), _cost(litellm, reply)


def _said(e):
    return str(getattr(e, "message", "") or e)[:300]


def _cost(litellm, reply):
    """The reply's cost in dollars by LiteLLM's price list; None for a model it has no price for (a self-hosted one)."""
    try:
        return litellm.completion_cost(completion_response=reply) or None
    except Exception:
        return None
