<div align="center">

<img src="https://raw.githubusercontent.com/chenxiachan/textfold/main/docs/assets/logo.svg" width="72" alt="TextFold">

# TextFold

**折叠任何文字，从一屏地图到每一个字**

你读的是原文，不是 AI 改写的版本。AI 只负责把它折叠成层级，帮你更高效地阅读。

🚀 **新功能：[在浏览器里用 TextFold](#在浏览器里用)！** 点一下浏览器扩展，就把正在读的网页传给 TextFold。<br>
支持 Chrome · Edge · Brave

<img alt="Python 3.9+" src="https://img.shields.io/badge/python-3.9%2B-6B5CE7?style=flat-square">
<img alt="在你的电脑上运行" src="https://img.shields.io/badge/runs-on_your_computer-E08A3C?style=flat-square">
<a href="https://github.com/chenxiachan/textfold/blob/main/LICENSE"><img alt="Apache 2.0 许可证" src="https://img.shields.io/badge/license-Apache_2.0-6B5CE7?style=flat-square"></a>

[English](https://github.com/chenxiachan/textfold/blob/main/README.md) · **简体中文**

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/chenxiachan/textfold/main/docs/assets/sources-dark.webp">
  <img src="https://raw.githubusercontent.com/chenxiachan/textfold/main/docs/assets/sources.webp" width="880" alt="TextFold 能读的：arXiv、PubMed Central、bioRxiv、medRxiv、维基百科、GitHub README、任意网页、PDF、Word、PowerPoint、Excel、EPUB、HTML、图片、Markdown、纯文本、CSV，以及 agent 的回答，从全文一路折到几个词">
</picture>

**粘贴链接：** 任意网页 · arXiv · PubMed Central · bioRxiv · medRxiv · 维基百科 · GitHub<br>
**拖入文件（一个或多个）：** PDF · Word · PowerPoint · Excel · EPUB · HTML · CSV · 图片 · Markdown · 纯文本<br>
**接上你的工具：** [Claude Code](#在-claude-code-和-pi-里) · [Pi](#在-claude-code-和-pi-里) · [Zotero](#直接读你的-zotero) · [Obsidian](#接上你的-obsidian) · [浏览器](#在浏览器里用)

**[试用演示 →](https://chenxiachan.github.io/textfold/gallery/)** 或者 **[下载软件 ↓](#开始使用)** · [最新 ↓](#最新)

<br>

<img src="https://raw.githubusercontent.com/chenxiachan/textfold/main/docs/assets/browser.gif" width="880" alt="Chrome 里的一篇维基百科文章，点一下 TextFold 按钮传进 TextFold，再一层层从全文折叠成章节地图">

</div>

## 读长文，脑子里少装一点

自由选择你需要的文字信息层级，降低认知负担。

| | 层级 | 每一段变成 | 谁的话 |
|:-:|---|---|---|
| **1** | **全文** | 原样的段落 | 作者 |
| **2** | **简述** | 一到三句关键原句，一字不改 | 作者 |
| **3** | **关键句** | 这一段的要点和理由 | AI，依据是简述里那几句 |
| **4** | **要点** | 一行，挂在本节的主线上 | AI |
| **5** | **主题** | 几个词；整篇变成一张章节卡片地图 | AI |

<img src="https://raw.githubusercontent.com/chenxiachan/textfold/main/docs/assets/zoom-zh.gif" width="880" alt="一篇论文从全文折叠成章节地图，再展开回来">

## 也可以用在 Agent 输出上

Agent 写的，总比你有空读的多。一个 [Claude Code mod](https://code.claude.com/docs/en/plugins/mods/overview) 用同样的方式折叠每条回答，一个键一个层级；Pi 里也有一个扩展做同样的事。[了解更多 ↓](#在-claude-code-和-pi-里)

<img src="https://raw.githubusercontent.com/chenxiachan/textfold/main/docs/assets/textfold.gif" width="880" alt="Claude Code 里的一条回答从全文折到每段两句，再折到一句，然后展开回来，每次按一个键">

<a name="在浏览器里用"></a>
<details>
<summary><h2>🚀 新功能：在浏览器里用</h2></summary>

Chrome、Edge 和 Brave 上的一个按钮，把你正在读的网页按你看到的样子发给 TextFold：登录后的、你付费订阅的、由网页脚本画出来的内容都一样。浏览器像阅读模式那样找出正文，保留标题、图、公式和脚注，再由 TextFold 折叠。TextFold 开着时，阅读器会在网页旁边打开。暂不支持 Firefox。

**从 GitHub 安装。** 按钮正在 Chrome 网上应用店审核，在那之前：

1. **安装 TextFold 0.10.40 或更新版本**（[下载](https://chenxiachan.github.io/textfold/zh.html#download)）并打开。它每次启动都会自动连上 Chrome、Edge、Brave 和 Chromium。用 Python 包的话：`python3 -m pip install -U textfold`，再运行一次 `python3 -m textfold capture install`。
2. **拿到 `extension` 文件夹。** [下载仓库 ZIP](https://github.com/chenxiachan/textfold/archive/refs/heads/main.zip) 并解压，或者 `git clone https://github.com/chenxiachan/textfold.git`。这个文件夹直接就能用，不需要构建。
3. **加载它。** 打开 `chrome://extensions`（Edge 里是 `edge://extensions`），打开 **开发者模式**，点 **加载未打包的扩展程序**，选 `extension` 文件夹。
4. **在网页上点它。** 从扩展菜单（拼图图标）把 TextFold 按钮固定到工具栏，然后在任何网页上点它。按钮上出现 ✓，说明网页已存进文献库。浏览器连不上 TextFold 时，按钮会打开一页，说明该怎么做。

文件夹不要挪走，浏览器是从它加载按钮的。要更新，就重新下载到原处，再在 `chrome://extensions` 里点按钮卡片上的 ↻。按钮上架 Chrome 网上应用店后，先移除这个副本，再从商店安装。要看到各层，先[连接模型 ↓](#连接模型)。

按钮只在你点它时读取网页，只交给本机的 TextFold，不交给任何别处（[隐私政策](https://chenxiachan.github.io/textfold/privacy.html#zh)）。原理见 [docs/web-capture.md](https://github.com/chenxiachan/textfold/blob/main/docs/web-capture.md)。

</details>

## 一种折叠，三个地方

- **读。** 粘贴链接、拖入文件，或在浏览器里点 TextFold 按钮，打开就是一张章节地图，再往里缩放。[开始使用 ↓](#开始使用)
- **折叠 agent 的回答。** 在 Claude Code 和 Pi 里一个键一个层级，不用第二个模型。[在 Claude Code 和 Pi 里 ↓](#在-claude-code-和-pi-里)
- **接入你的工作流。** `python3 -m textfold get` 把文档一层一层地交给你的 agent 或脚本；开放的格式让任何程序都能读写折叠后的文字。[给 agent 用 ↓](#给-agent-和脚本用)

## 你会得到

- **词会移动，不会被换掉。** 缩放时，留下的词滑到新的位置，其余的在周围淡出。
- **缩放跟着手指走。** 可以停在半路、往回退，也可以只展开一段。

- **对照原文。** 论文或 PDF 的原页可以放在旁边，读到哪一段，就标出它在原页上的位置。
- **针对内容问 AI。** 选中一段，或者就全文提问：先给简短的答案，每一条都链接到它依据的段落。打开 **上网查找**，本机的 Agent 也会上网搜索。
- **笔记跟着缩放。** 在页边高光、写笔记，缩小时标过的内容依然在，也会和整页一起保存。

- **用你的语言读。** 支持多种语言，逐句翻译，公式保持原样。文档自己的各层级用它本身的语言写。

- **你的文献库，井井有条。** 用 ⌘K 搜索；建文件夹整理文档，拖进去或者右键移进去；按类型、添加时间或最近打开排序。

<table>
<tr>
<td width="50%"><img src="https://raw.githubusercontent.com/chenxiachan/textfold/main/docs/assets/notes.png" alt="全文里的高光和页边笔记"></td>
<td width="50%"><img src="https://raw.githubusercontent.com/chenxiachan/textfold/main/docs/assets/keep.png" alt="高光过的句子在要点层被保留"></td>
</tr>
<tr>
<td align="center"><sub>在全文里高光、在页边写笔记……</sub></td>
<td align="center"><sub>……缩小时，标过的内容依然在。</sub></td>
</tr>
<tr>
<td width="50%"><img src="https://raw.githubusercontent.com/chenxiachan/textfold/main/docs/assets/map.png" alt="论文在主题层画成思维导图：各章节和它们的主题"></td>
<td width="50%"><img src="https://raw.githubusercontent.com/chenxiachan/textfold/main/docs/assets/original.png" alt="论文自己的 PDF 放在正文旁边，正在读的段落在原页上标出"></td>
</tr>
<tr>
<td align="center"><sub>把全文看成一张思维导图……</sub></td>
<td align="center"><sub>……或者对照原文来读。</sub></td>
</tr>
<tr>
<td width="50%"><img src="https://raw.githubusercontent.com/chenxiachan/textfold/main/docs/assets/highlights.png" alt="关键句层的高光：在关键句层做的是紫色，在要点层做的是蓝色"></td>
<td width="50%"><img src="https://raw.githubusercontent.com/chenxiachan/textfold/main/docs/assets/select.png" alt="选中一段后出现的工具条：高光、笔记、提问"></td>
</tr>
<tr>
<td align="center"><sub>每处高光保留它所在层级的颜色……</sub></td>
<td align="center"><sub>……任何一段都可以写笔记或提问。</sub></td>
</tr>
</table>

## 开始使用

**[下载软件](https://chenxiachan.github.io/textfold/zh.html#download)**，有 macOS（Apple 芯片或 Intel）和 Windows 版。需要的东西都在里面，包括 Python。

也可以装 Python 包，任何系统都能用，也能接进你自己的流程：

```bash
python3 -m pip install textfold
python3 -m textfold serve
```

在浏览器里打开 `http://localhost:3017`。第一次使用先[连接一个模型](#连接模型)，然后粘贴链接或拖入文件，再选择语言。需要 Python 3.9 或更新的版本（macOS 自带的就可以）。

| 粘贴或拖入 | TextFold 读到的 |
|---|---|
| 论文的链接或 DOI：arXiv、PubMed Central、bioRxiv、medRxiv | 网站上结构化的正文（arXiv 论文没有 HTML 版本时读它的 PDF；其他 DOI 要在 Europe PMC 有开放全文） |
| 其他任何网页 | 它的正文，去掉菜单、广告，以及网页对读者隐藏的文字 |
| 浏览器里打开的网页 | 你看到的样子，登录后、订阅后、由脚本画出来的内容都在：点 TextFold 按钮。[在浏览器里用 ↑](#在浏览器里用) |
| 维基百科条目、GitHub 仓库 | 条目；README，连同里面的图 |
| PDF | 如果它是网上能读到的论文，就读网上的版本，你的文件放在旁边对照；其他 PDF 在你的电脑上读：不装引擎 TextFold 自己就能读，图也会裁出来；装了 PDF 引擎读得更完整（MinerU，约 3.4 GB，在首页安装一次即可），扫描件需要它 |
| Word、PowerPoint、Excel 文件，CSV、EPUB、保存的网页 | 文档本身，标题、表格、图片和公式都保留：不装引擎 TextFold 自己就能读，装了 PDF 引擎读得更完整 |
| 图片 | 由 PDF 引擎识别出的文字 |
| Zotero 文献库里的条目 | 有 arXiv 版本就读 arXiv，否则读它的 PDF 或其他附件，再没有就按 DOI 读。[直接读你的 Zotero ↓](#直接读你的-zotero) |
| Markdown 或纯文本文件 | 原样的文件 |

读过的文档保存在系统存放应用数据的文件夹里，和桌面 App 共用。

<details>
<summary>也可以直接从代码运行</summary>

```bash
git clone https://github.com/chenxiachan/textfold.git
cd textfold
python3 -m pip install -r requirements.txt
python3 -m textfold serve
```

这样文档保存在代码目录里。

</details>

## 连接模型

TextFold 用你已有的模型，不需要注册账号。启动时它会自己去找；一个都没找到时，首页会给出接入的方式。

| 你有 | 怎么做 |
|---|---|
| Claude Code、Codex、Pi 或 WorkBuddy | 什么都不用做，TextFold 会自动找到并使用。WorkBuddy（试验）用你登录的账号和它的模型；单独安装的 CodeBuddy 命令行也可以。 |
| ChatGPT 订阅 | 点 **使用我的 ChatGPT 订阅**。电脑上会启动一个小的本地桥，用你的账号登录（需要 Node.js）。 |
| 什么都还没有 | 点 **OpenRouter 一键授权**。授权一次，免费模型马上可用。 |
| 一个 API key | **设置 › 添加供应商**：Anthropic、OpenAI、DeepSeek、通义千问、智谱 GLM、Kimi、MiniMax、Google、Ollama，或任何 OpenAI 兼容接口。 |

回答问题可以用比生成层级更强的模型：**设置 › 模型 › 回答问题**，还可以选推理强度。

## 阅读时的操作

| 操作 | 作用 |
|---|---|
| 双指捏合，或 ⌘ / Ctrl 加滚轮 | 围绕鼠标所在处缩放 |
| `1` 到 `5` | 跳到某一层 |
| 主题层缩放条旁的 **卡片** 或 **导图** | 用卡片或思维导图看各节和主题词 |
| 点击某一段 | 只展开这一段 |
| 点击图片 | 放大查看 |
| 顶部的 **原文** | 对照原文 PDF 阅读 |
| 顶部的 **保存** | 把整页存成一个文件，或按当前层级存成 Markdown，或者只存你的标注 |
| 顶部的 **Aa** | 选浅色或深色、默认或纸张风格，调字号、字体、行距、栏宽，选页边显示哪些内容 |
| `F` | 专注模式：只留正文，各栏在屏幕边缘出现 |
| **Aa** 里的 **全屏** | 窗口占满整个屏幕（Mac 上 ⌃⌘F，Windows 上 F11） |
| 选中文字 | 高光、写笔记或追问 |
| 右下角 **问全文** | 就整篇提问（问题旁边的 **上网查找**：本机的 Agent 也上网搜索） |
| `L` | 切换到下一种语言 |
| `⌘F`（Ctrl+F） | 在文中查找：当前层级显示的文字，以及这一层折叠掉的原文（跳到那里时，那一段原地展开到全文）；Enter 和 ⇧Enter 逐个跳转 |
| `⌘K`（Ctrl+K）或 `/` | 搜索文献库 |
| 在侧栏里 ⌘ / Ctrl 或 Shift 加点击 | 选中多篇，一起移进文件夹或删除 |
| 侧栏顶部的图钉 | 让侧栏一直开在正文旁边 |

## 接上你的工具

从 Zotero 文献库带进文字，把读到的内容链接到 Obsidian 笔记，在你工作的地方折叠 Agent 的回答。（浏览器：见[上面 ↑](#在浏览器里用)。）

### 直接读你的 Zotero

点拖放框下面的 **Zotero 文献库**，你的文献库就按 Zotero 里的样子出现：分类、群组文献库、搜索都在。任何条目点 **阅读** 就能打开。

- **arXiv 上有的条目**，从 arXiv 读。其他条目读它的 PDF，PDF 放在旁边对照；没有 PDF 就读它附带的 EPUB、保存的网页或 Word 文件。只有 DOI 的条目，从 Europe PMC 读。
- **你在 Zotero 里填的信息都保留**：标题、作者和 citekey。
- **一切都在你的电脑上。** TextFold 读的是你已经在用的 Zotero 应用，和引文插件的方式一样：不需要账号和密钥，不向 Zotero 的服务器发送任何东西，也不会改动你的文献库。

打开方法：在 Zotero 7 里勾选 **设置 › 高级 › 允许此计算机上的其他应用程序与 Zotero 通信**。

### 接上你的 Obsidian

[TextFold Bridge](https://github.com/adalach/obsidian-textfold-bridge) 是 [@adalach](https://github.com/adalach) 写的 Obsidian 插件，把你的笔记和你在 TextFold 里读的东西连起来，就像 Zotero 和它的引用插件那样：文档和你在上面写的东西留在 TextFold，笔记里留着指回去的链接。

- **你自己的笔记也能折叠。** 在 Obsidian 里按 TextFold 的五个层级读一篇笔记，词语像在阅读器里一样移动。改过之后点 **Refold**，模型只重写改了的小节，高亮会在新文字里找回原来的位置。
- **从阅读器拖进笔记。** 一段、一整节、选中的文字、主题导图上的卡片或你的一条标注，拖进笔记就成了一个 callout，带着标题、所在的节和回到原处的链接。
- **链接打开到原处。** `textfold://` 链接直接打开文档里的那一段、那条标注或那个层级。在 Obsidian 里悬停就能读到那段原文。
- **TextFold 里的标注就在笔记旁边。** 侧栏列出这篇笔记引用的段落，以及它提到的文档上你做的高亮和提问。也可以把它们导入成一篇单独的笔记，以后更新时不会丢掉你在里面写的内容。

**安装步骤：**

1. **安装 TextFold：** [下载 app](https://chenxiachan.github.io/textfold/#download)（macOS 或 Windows），或者安装 Python 包（见[开始使用 ↑](#开始使用)）。
2. **打开 TextFold**（app，或者 `python3 -m textfold serve`），用插件的时候它要开着。需要 TextFold 0.10.18 或更新的版本，插件只通过 127.0.0.1 和它通信。
3. **在 Obsidian 里：** **设置 › 第三方插件 › 关闭安全模式 › 浏览**，搜 **TextFold Bridge**，然后**安装**、**启用**。
4. **用的是桌面版 app 的话，** 建议把 **设置 › TextFold Bridge › Links › Open links in** 改成 **TextFold app**，默认是用浏览器打开。

### 在 Claude Code 和 Pi 里

TextFold 把五个层级放到 Claude Code 和 Pi 的每条回答上。按 5 看一句话的答案，按 3 看每段的要点，按 1 看每一个字（在 Pi 里要按住 Alt）。不做任何改写，所以无论在哪一级，你读到的都是模型的原话。

<details>
<summary><b>在 Claude Code 里：安装，以及每一级能看到什么</b></summary>

<br>

<img src="https://raw.githubusercontent.com/chenxiachan/textfold/main/docs/assets/textfold.gif" width="880" alt="Claude Code 里的一条回答从全文折到每段两句，再折到一句，然后展开回来，每次按一个键">

需要 Claude Code 2.1.287 或更新的版本，在终端里或桌面应用的 Code 标签页里都能用。

```bash
claude plugin marketplace add chenxiachan/textfold
claude plugin install textfold@textfold
```

| 键 | 层级 | 你看到的 |
|:-:|---|---|
| **1** | **Full** | Claude 写的每一个字 |
| **2** | **Brief** | 每个要点，连同背后的理由 |
| **3** | **Points** | 只看要点，一眼扫完 |
| **4** | **Outline** | 长回答的地图：有哪几节，每节讲了什么（没有标题的回答显示要点） |
| **5** | **Gist** | 一句话的答案 |

在提示框为空时输入数字，或者在提示框上方那一行里选一个层级。短回答保持原样：不到大约 80 个词（中文约 130 字），或者折叠后几乎不会变短的回答，总是原样显示。

**默认层级。** 每个会话默认从 Full 开始。想换成别的层级，运行 `/config`，设置 **Starting level**；会话里照样可以用提示框上方那一行或数字键 1 到 5 切换。

**为什么折叠可信。** TextFold 请 Claude 把要点放在前面：每条回答先给答案，每段先写关键句。折叠保留的就是 Claude 放在最前面的内容。没有第二个模型，也不做摘要，所以折叠不花钱、不用等。

详见 [plugins/textfold](https://github.com/chenxiachan/textfold/blob/main/plugins/textfold/README.md)。

</details>

<details>
<summary><b>在 Pi 里：安装，看它怎么折叠</b></summary>

<br>

<img src="https://raw.githubusercontent.com/chenxiachan/textfold/main/docs/assets/pi-think.gif" width="880" alt="Pi 里的一轮编程对话，思考过程和回答一起逐级折叠，直到各剩一句，用按键，也用鼠标点级别行">

一个扩展把同样的五个层级放到 [Pi](https://pi.dev) 的每条回答上。需要 Pi 1.2.0 或更新的版本，Node 22.15 或更新。

```bash
git clone https://github.com/chenxiachan/textfold.git
pi install ./textfold/plugins/pi-textfold
```

按住 Alt 再按数字，点提示框上方那一行里的层级，或者输入 `/fold` 加层级：`/fold 3`、`/fold points`、`/fold off`。每条回答都在原处折叠，之前的回答也一样，模型的思考过程也一起折叠，很长的一轮编程对话几行就能读完。层级跟着会话保存，`/fold calibrate` 能在任何键盘上教会按键。在 Mac 上要让终端把 Option 当作 Meta，否则 Option+1 打出的是 ¡。它用的就是 mod 的折叠引擎，所以同一条回答在 Claude Code 和 Pi 里每一级读到的都一样。

详见 [plugins/pi-textfold](https://github.com/chenxiachan/textfold/blob/main/plugins/pi-textfold/README.md)。由 [@jlamweil](https://github.com/jlamweil) 贡献。

</details>

## 给 agent 和脚本用

`python3 -m textfold get` 一次给出一份文档的一个层级，从最概括的开始。agent 可以先读地图，再决定把上下文花在哪一节：

```bash
python3 -m textfold get https://arxiv.org/abs/2512.23916        # 地图：每一节的主题
python3 -m textfold get 2512.23916 --level key --section 3      # 某一节：每个要点和它的理由
python3 -m textfold get report.pdf --json                       # 整份文档，TextFold 格式
```

还没折叠过的文档会先用你连接的模型折叠。模型写的每一行都标明是模型写的，并且指回它依据的作者原句：[docs/format.md](https://github.com/chenxiachan/textfold/blob/main/docs/format.md) 说明了这个格式，任何读写折叠文本的程序都可以用。

运行中的 TextFold（应用，或 `python3 -m textfold serve`）会在数据文件夹的 `server.json` 里写下自己在哪里（Mac 上是 `~/Library/Application Support/TextFold`，Windows 上是 `%LOCALAPPDATA%\TextFold`，Linux 上是 `~/.local/share/textfold`，从克隆的仓库运行时就是仓库本身）：`{"format": "textfold/server", "version": 1, "url": "http://127.0.0.1:<port>", "port": <port>, "pid": <pid>, "textfold": "<version>", "started": "<UTC time>"}`。服务器停止时这个文件随之删除；留下来的文件（服务器被直接结束时）指向的地址不会再有回应。

在支持 WebMCP（一个还在起草的网页标准，已在 Codex 自带的浏览器里试过）的浏览器里，agent 能在 TextFold 的页面上找到它自己的操作，当作工具来用：读你正在读或选中的段落；在你要求时，在一段确切的原文上存笔记、导入文件或 Zotero 条目、整理文件夹。不支持的浏览器一切照旧。工具列表见 [docs/webmcp.md](https://github.com/chenxiachan/textfold/blob/main/docs/webmcp.md)。由 [@peitianyi23-sudo](https://github.com/peitianyi23-sudo) 贡献。

## 保存一页

阅读页顶部的 **保存** 把一页存成一个 HTML 文件，不需要服务器，在哪里都能打开：各个层级、你选的标注，还可以带上原文 PDF 在左边对照。也可以按正在读的层级把整篇存成 Markdown，标注放在各自段落下面（在主题层存就是一份大纲，导图插件能直接画出来）；或者只存你的标注，存成网页、PDF、Obsidian 用的 Markdown 或 JSON。命令行也能导出这一页，只是不带你的标注：

```bash
python3 -m textfold export 2512.23916
```

TextFold 只是一个工具，它的许可证只管代码。分享文件时，请遵守原文的许可条款，它标在每篇的标题下方。

## 最新

- **2026 年 10 月 28 日：** 浏览器里的按钮。
- **2026 年 10 月 27 日：** 标签留作标签。
- **2026 年 10 月 27 日：** WorkBuddy 5.8、会走的进度，存下的网页读出正文。
- **2026 年 10 月 26 日：** 提问从全文回答，网页的脚注单独保留，下载会再试。

<details>
<summary>每条的完整内容，以及更早的更新</summary>

- **2026 年 10 月 28 日：浏览器里的按钮。** 在 Chrome、Edge 或 Brave 里点一下，就把正在读的网页按你看到的样子发给 TextFold，登录后、订阅后、由脚本画出来的内容都一样，阅读器在网页旁边打开。正文在浏览器里像阅读模式那样找出来，保留标题、图、公式和脚注：ScienceDirect 上的一篇论文保留了 32 个标题和 9 张图。App 每次启动都会自动连上浏览器，按钮正在上架 Chrome 网上应用店。另外，论文的每一节都知道自己是不是附录，依据是论文本身的标记。感谢 [@minatomugino864-lgtm](https://github.com/minatomugino864-lgtm)。[在浏览器里用 ↑](#在浏览器里用)
- **2026 年 10 月 27 日：标签留作标签。** 不属于正文论述的一行，比如“Keywords:”、PDF 引擎读成正文的标题、作者的地址或日期，在每一层都保留原文。以前模型会为它写一句话，比这一行本身还长（“Section one is the Introduction.”）；现在它照原样留着。已经生成过的文档，在语言菜单里选 **重新生成** 后就会这样显示。
- **2026 年 10 月 27 日：WorkBuddy 5.8、会走的进度，存下的网页读出正文。** WorkBuddy 5.8 带了一个精简版的 CodeBuddy，TextFold 现在按 WorkBuddy 自己的方式运行它。这个精简版的登录只供 WorkBuddy 自己使用，所以 TextFold 用不了时会直接说明，并告诉你怎么办：单独安装 CodeBuddy 并在那里登录，TextFold 就会用它。生成时每一节回来就计一节，不会再因为一节慢就停在 0。存下的网页如果唯一的 `<article>` 是广告，或者读者评论比文章还长，现在读到的是文章本身。编号列表折叠后保留编号。感谢 [@HamiltonZeng](https://github.com/HamiltonZeng)、[@Yanksi](https://github.com/Yanksi)、[@Nombah501](https://github.com/Nombah501) 和 [@minatomugino864-lgtm](https://github.com/minatomugino864-lgtm)。
- **2026 年 10 月 26 日：提问从全文回答，网页的脚注单独保留，下载会再试。** 对一段话或整篇提问时，模型拿到的是全文，不只是那一段和几段与问题用词相同的段落，所以能用上远处换了说法讲同一件事的部分。太长放不下的文本仍按原来的方式提问。鼠标指向编号才显示脚注和参考文献的网页，现在把它们保留为脚注，用网页自己的标记，不再把内容混进正文的句子：全文层读起来和网页一样。下载中断或服务器忙时会再试，试了五次仍不行，TextFold 会说明连不上哪个网站，并给出 **继续**。“放在这里”的提示在文件移出窗口时立刻消失。感谢 [@RocStone](https://github.com/RocStone)、[@QZGao](https://github.com/QZGao)、[@minatomugino864-lgtm](https://github.com/minatomugino864-lgtm) 和 [@QAbot-zh](https://github.com/QAbot-zh)。
- **2026 年 10 月 25 日：公式和表格像 Typora 那样显示。** Markdown 文档里的公式由 KaTeX 排版，字体和间距与 TeX 一致。单独成行的公式按 Typora 的方式在右边编号 (1)、(2)：`\tag{A}` 给出它自己的编号，`\notag` 和带星号的环境不编号，`\eqref{…}` 显示 `\label{…}` 那条公式的编号，点它就跳过去。多行的公式（`aligned`）分行显示。论文里的公式改用 LaTeX 的字体 Latin Modern Math。来自 Markdown、网页或文档的表格，每一格都有边框，隔行有底色。单独成行的公式也能像文字一样选中，用来高光、写笔记或提问；也能像段落一样用左边的手柄拖出去，带着编号和指回原处的链接。书库里已有的 Markdown 文档也会这样显示，层级和笔记都保持原样。
- **2026 年 10 月 25 日：文档用它自己的语言，提问可以上网查，加入泰语。** 文档的各层级用它本身的语言写，语言菜单也用这个名字：中文文档显示为“简体中文”，用中文读时不再让模型把它再翻译成中文一遍。英文和其他语言一样，是一份翻译。提问时打开 **上网查找**，本机的 Agent（Claude Code、Codex、CodeBuddy，或装了网页扩展的 Pi）会搜索、打开网页来回答，用到的网页附上链接；它不会改动任何文件。在设置里选好的文献库文件夹会立刻保存。泰语加入可读的语言。感谢 [@mingrath1](https://github.com/mingrath1)、[@MacMillan1225](https://github.com/MacMillan1225)、[@RocStone](https://github.com/RocStone) 和 [@QAbot-zh](https://github.com/QAbot-zh)。
- **2026 年 10 月 25 日：在文中查找，画出图表，文献库放在你选的文件夹。** ⌘F（Ctrl+F）在文中查找：查当前层级显示的文字，也查这一层折叠掉的原文，跳到这样一处时，那一段原地展开到全文。Markdown 文件或网页里用 Mermaid 写的图会画出来，页面是浅色就画成浅色，是深色就画成深色。在设置的 **文献库位置** 里，可以把文献库复制到另一个文件夹，以后用那里的；也可以打开一个文件夹里已有的文献库。TextFold 下次启动时切换，原来那份文献库保持原样。感谢 [@adalach](https://github.com/adalach)、[@Yanksi](https://github.com/Yanksi) 和 [@kvistgaard](https://github.com/kvistgaard)。
- **2026 年 10 月 24 日：高光分深浅；印地语按印地语来读。** 高光仍是划它的那一层的颜色，再分浅、中、深三档：在选中文字上方的工具条里点 **高光** 旁边的圆点，或者打开笔记再改。**Aa** 里每一层的颜色可以从八种里选。把主题卡片拖进笔记时，标题下面的摘要也一起带过去。印地语的词不再被拆开，句子在 danda（।）处结束。公式里 `\text{}` 中的文字照原样显示（50% off，而不是 `50\% off`），Word 公式里的普通文本也不会再让整个公式出错。Markdown 文档的 front matter 按 Quarto 的写法给出作者，不再把作者单位当成作者。Python 包在 Python 3.9、3.10 和 3.11 上又能启动了。感谢 [@vcdim](https://github.com/vcdim)、[@Pharex38](https://github.com/Pharex38) 和 [@adalach](https://github.com/adalach)。
- **2026 年 10 月 23 日：链接一直指得准；Windows 上找得到 WorkBuddy。** 段落和章节的 id 在文档的整个生命周期里都不变：改了文字、PDF 重新转换、TextFold 升级以后都一样。所以从 Obsidian 或笔记里点过来的链接，还能打开原来的地方。指向已经不在文中的段落的链接，会打开它原来所在的章节，并提示一句。在 Windows 上，WorkBuddy 5.8 又能找到了，装在哪个盘都行。感谢 [@adalach](https://github.com/adalach) 和 [@LyotoDS](https://github.com/LyotoDS)。
- **2026 年 10 月 23 日：划了什么就亮什么。** 在关键句和要点层划的高光，选中的字全部亮起，模型写的词也亮。以前只亮由原文来的词，只选了模型的话时一个都不亮。每层一种颜色：全文和简述是黄色，关键句是紫色，要点是蓝色。层与层不再互相点亮，**保留我的高光** 照旧把原文的高光带到关键句和要点层。笔记框里按 Enter 保存并关闭，Shift+Enter 换行（手机上按 Enter 换行）。换一篇文档时不再重新下载应用的脚本和样式，在服务器上部署、用手机读时最明显。感谢 [@adalach](https://github.com/adalach) 和 [@LumenYoung](https://github.com/LumenYoung)。
- **2026 年 10 月 22 日：精确转换后语言和笔记都在；手机上更好读。** PDF 重新精确转换后，原来有的每种语言都会按新文本重新生成，你在上面做的笔记也会跟着原文找到新的位置。快速转换时的翻译不会再落到新文本上（显示为已过期，而且对不上段落）。过期的翻译在语言菜单最上面有 **更新**。在手机上，打开侧栏的按钮不再压住标志，很宽的公式在自己的框里横向滚动。Markdown 文档和转换来的 PDF 里的长公式在任何屏幕上都保留公式框，在列表里也会显示。指向拆开的段落后半部分的 `textfold://` 链接能打开了。感谢 [@LumenYoung](https://github.com/LumenYoung) 和 [@adalach](https://github.com/adalach)。
- **2026 年 10 月 22 日：按 TeX 原样显示的图；全屏。** arXiv 论文里由浏览器排字的图（TikZ 画的图）有时字会溢出框，arXiv 自己的网页也一样。这类图下面有一个按钮，点了就从论文的 PDF 里取 TeX 排好的原图，不点就不下载；换过的图下面可以一键换回来。**Aa** 里可以 **全屏**；对照原文时也能打开主题层的导图。简述层里进入下一层的词和其他词的深浅差小了一些，纸张风格的字色和默认风格一致。
- **2026 年 10 月 22 日：更省磁盘空间。** 新进来的图片存成 WebP：一篇 arXiv 论文的插图只占原来五分之一甚至更少，而且同时下载 4 张，新论文打开更快。文献库里已有的图片保持原样。应用为显示过的图片和 PDF 留的缓存不超过 32 MB，更新安装好以后会删掉下载的安装包。删除的文档在 papers/.trash 里保留 30 天，之后彻底清除。在关键句和要点层，由几个子图组成的插图改为并排显示，不再上下叠在一起被截断。
- **2026 年 10 月 21 日：更适合长时间阅读。** **Aa** 里可以选浅色或深色（或跟随系统），再选一种比白底柔和的纸张风格，两者可以搭配；还能调行距。再打开一篇文档会回到上次读到的地方。按 `F` 进入专注模式，屏幕上只留正文。侧栏可以固定在正文旁边。
- **2026 年 10 月 21 日：给浏览器里的 agent 用的工具。** 在支持 WebMCP 的浏览器里，agent 能把 TextFold 自己的操作当作工具：读你选中的段落；在你要求时，在这段原文上存笔记、导入文件或 Zotero 条目、整理文件夹。[工具列表](https://github.com/chenxiachan/textfold/blob/main/docs/webmcp.md)。感谢 [@peitianyi23-sudo](https://github.com/peitianyi23-sudo)。
- **2026 年 10 月 21 日：主题层可以看成思维导图。** 在主题层，缩放条旁的 **导图** 把各节和主题词画成一张思维导图：点主题词读那一段，点章节可以收起。标题层级更分明了；**Aa** 可以隐藏页边内容（段落关联、AI 问答、你的笔记）；**保存** 可以按正在读的层级把整篇存成 Markdown。Windows 安装时可以选安装目录。
- **2026 年 10 月 20 日：字号、栏宽和字体；侧栏多选。** 顶部的 **Aa** 可以调字号和栏宽，选衬线或无衬线，对所有文档都生效。在侧栏里 ⌘ 点击（Ctrl 点击）或 Shift 点击可以选中多篇，一起移进文件夹或删除；删除后几秒内可以撤销。
- **2026 年 10 月 20 日：一次拖入多个文件。** 一次拖入或选择多个文件，它们会一个接一个读取并生成层级，你可以接着读别的，侧栏里能看到每一个的进度。
- **2026 年 10 月 20 日：TextFold 和 Obsidian。** Obsidian 插件 [TextFold Bridge](https://github.com/adalach/obsidian-textfold-bridge) 让笔记里的链接直接指到 TextFold 里的某一段、某一节或某条标注。把阅读器里的段落、整节或选中的文字拖进笔记，会带上回到原处的链接。你自己的笔记也能在 Obsidian 里按五个层级折叠，改过之后原地重新折叠，标注会在新文字里找回原来的位置。感谢 [@adalach](https://github.com/adalach)。
- **2026 年 10 月 20 日：Pi 的思考过程也能折叠了。** 在 Pi 里，模型的思考过程和回答一起折叠，很长的一轮编程对话几行就能读完。点一下级别行就能折叠，接着上次的会话时级别不变，用 `/fold calibrate` 教一次按键，任何键盘都能用。感谢 [@jlamweil](https://github.com/jlamweil)。
- **2026 年 10 月 19 日：更多阅读语言。** 新增瑞典语、丹麦语、挪威语、芬兰语、荷兰语、波兰语、捷克语、乌克兰语、土耳其语、印尼语和越南语。界面没有这种语言时显示英文。感谢 [@Kullenej](https://github.com/Kullenej)。
- **2026 年 10 月 15 日：Pi 的回答也能折叠了。** [Pi 扩展](https://github.com/chenxiachan/textfold/blob/main/plugins/pi-textfold/README.md) 把五个层级放到每条回答上，折法和 Claude Code 里一样。感谢 [@jlamweil](https://github.com/jlamweil)。
- **2026 年 10 月 15 日：不装 PDF 引擎也能读 PDF。** 自带文字的 PDF（大多数论文都是）直接打开，图从原页上裁出来；只有扫描件还需要 PDF 引擎。没有 HTML 版本的 arXiv 论文改读它的 PDF。感谢 [@sylvainHellin](https://github.com/sylvainHellin)。
- **2026 年 10 月 15 日：你的文献库和你的提问。** 文献库可以搜索、建文件夹，按添加时间或最近打开排序。提问有了发送和停止键，对话里任何一轮都可以删掉，回答问题还可以用比生成层级更强的模型。感谢 [@suhyungJang](https://github.com/suhyungJang)。
- **2026 年 10 月 14 日：Mac 和 Windows 软件。** 下载 TextFold 的 macOS 版（Apple 芯片或 Intel，已签名并经苹果公证）或 Windows 版，需要的东西都在里面。它会找到你电脑上的 Claude Code、Codex 或 Pi，有新版本时经你同意自己更新。[下载 ↑](#开始使用)
- **2026 年 10 月 14 日：直接读你的 Zotero 文献库。** 拖放框下面的 **Zotero 文献库** 按 Zotero 里的样子列出你的文献库和分类，任何条目点一下就能打开：arXiv 上有就读 arXiv，没有就读它自己的 PDF。感谢 [@adalach](https://github.com/adalach)。[直接读你的 Zotero ↑](#直接读你的-zotero)
- **2026 年 10 月 14 日：读 arXiv 页面更安全了。** 页面里可能运行脚本的部分，会在阅读器显示之前去掉，图都保持原样。感谢 [@neil-patel77](https://github.com/neil-patel77)。
- **2026 年 10 月 13 日：把一页存成一个文件。** 阅读页顶部的 **保存** 把整页存成一个 HTML 文件，可以带上标注和原文 PDF；也可以只存标注。[保存一页 ↑](#保存一页)
- **2026 年 10 月 13 日：Word、PowerPoint、Excel、电子书等文件。** 拖入 Word、PowerPoint、Excel 文件，或 CSV、EPUB、保存的网页，马上就能打开，公式也在，不用另装任何东西。
- **2026 年 10 月 13 日：完整的图，和使用说明。** PDF 引擎拆成碎块的图，现在从原页上整幅裁出来。第一次打开会弹出一份简短的使用说明，之后可以从 **使用说明** 再打开。
- **2026 年 10 月 9 日：PaperFold 改名为 TextFold。** 还是同一个阅读器，新名字涵盖它能折叠的一切：论文、网页、PDF、README，以及 agent 的回答。旧仓库的链接会自动跳到这里，设置也会自动搬过来。
- **2026 年 10 月 9 日：任意网页和 PDF。** 粘贴任何网页的链接，就能在五个层级上读它的正文，网页对读者隐藏的文字会被去掉。拖入一个 PDF：如果它是一篇网上能读到的论文，就打开网上的版本，你的 PDF 放在旁边对照；其他 PDF 在你的电脑上转换。
- **2026 年 10 月 9 日：给 agent 用。** `python3 -m textfold get <链接> --level topic` 输出一份文档的地图，`--section` 再展开需要的那一节。[了解更多 ↑](#给-agent-和脚本用)
- **2026 年 10 月 8 日：读任何 GitHub README。** 粘贴仓库链接，就能在五个层级上读它的 README，logo、徽章和动图都和 GitHub 上一样。
- **2026 年 10 月 8 日：对照原文读。** 论文页顶部的 **原文** 会在左边打开它的 PDF，读到哪一段，就标出它在原页上的位置。新论文解析完就能打开，缩放层级在后台生成。
- **2026 年 10 月 7 日：Agent 的回答也能折叠了。** [Claude Code mod](#在-claude-code-和-pi-里) 把五个层级放到每条回答上。按 5 看一句话的答案，按 1 看每一个字。
- **2026 年 10 月 6 日：不只读 arXiv。** PubMed Central、bioRxiv、medRxiv、任何语言的维基百科，以及你自己的 Markdown 文件。

</details>

<br>

## 贡献者

<a href="https://github.com/neil-patel77" title="@neil-patel77"><img src="https://github.com/neil-patel77.png?size=80" width="40" height="40" alt="@neil-patel77" /></a>
<a href="https://github.com/adalach" title="@adalach"><img src="https://github.com/adalach.png?size=80" width="40" height="40" alt="@adalach" /></a>
<a href="https://github.com/suhyungJang" title="@suhyungJang"><img src="https://github.com/suhyungJang.png?size=80" width="40" height="40" alt="@suhyungJang" /></a>
<a href="https://github.com/sylvainHellin" title="@sylvainHellin"><img src="https://github.com/sylvainHellin.png?size=80" width="40" height="40" alt="@sylvainHellin" /></a>
<a href="https://github.com/jlamweil" title="@jlamweil"><img src="https://github.com/jlamweil.png?size=80" width="40" height="40" alt="@jlamweil" /></a>
<a href="https://github.com/lamegoat" title="@lamegoat"><img src="https://github.com/lamegoat.png?size=80" width="40" height="40" alt="@lamegoat" /></a>
<a href="https://github.com/Kullenej" title="@Kullenej"><img src="https://github.com/Kullenej.png?size=80" width="40" height="40" alt="@Kullenej" /></a>
<a href="https://github.com/666xz666" title="@666xz666"><img src="https://github.com/666xz666.png?size=80" width="40" height="40" alt="@666xz666" /></a>
<a href="https://github.com/peitianyi23-sudo" title="@peitianyi23-sudo"><img src="https://github.com/peitianyi23-sudo.png?size=80" width="40" height="40" alt="@peitianyi23-sudo" /></a>
<a href="https://github.com/mcardZH" title="@mcardZH"><img src="https://github.com/mcardZH.png?size=80" width="40" height="40" alt="@mcardZH" /></a>
<a href="https://github.com/momoko-sketch" title="@momoko-sketch"><img src="https://github.com/momoko-sketch.png?size=80" width="40" height="40" alt="@momoko-sketch" /></a>
<a href="https://github.com/Knoety" title="@Knoety"><img src="https://github.com/Knoety.png?size=80" width="40" height="40" alt="@Knoety" /></a>
<a href="https://github.com/LumenYoung" title="@LumenYoung"><img src="https://github.com/LumenYoung.png?size=80" width="40" height="40" alt="@LumenYoung" /></a>
<a href="https://github.com/LyotoDS" title="@LyotoDS"><img src="https://github.com/LyotoDS.png?size=80" width="40" height="40" alt="@LyotoDS" /></a>
<a href="https://github.com/Pharex38" title="@Pharex38"><img src="https://github.com/Pharex38.png?size=80" width="40" height="40" alt="@Pharex38" /></a>
<a href="https://github.com/vcdim" title="@vcdim"><img src="https://github.com/vcdim.png?size=80" width="40" height="40" alt="@vcdim" /></a>
<a href="https://github.com/Yanksi" title="@Yanksi"><img src="https://github.com/Yanksi.png?size=80" width="40" height="40" alt="@Yanksi" /></a>
<a href="https://github.com/mingrath1" title="@mingrath1"><img src="https://github.com/mingrath1.png?size=80" width="40" height="40" alt="@mingrath1" /></a>

欢迎贡献：先看 [CONTRIBUTING.md](https://github.com/chenxiachan/textfold/blob/main/CONTRIBUTING.md)。

<br>

<div align="center">

**如果 TextFold 让阅读轻松了一些，点个 ⭐ star，让更多人看到它。**

</div>

---

<div align="center">

<sub>TextFold 建立在这些开源项目之上：[trafilatura](https://github.com/adbar/trafilatura) 从网页中找出正文，[Defuddle](https://github.com/kepano/defuddle) 和 [Readability](https://github.com/mozilla/readability) 在浏览器里找出正文，[pdf-inspector](https://github.com/firecrawl/pdf-inspector) 读取 PDF 自带的文字，[PDFium](https://pdfium.googlesource.com/pdfium/)（经由 [pypdfium2](https://github.com/pypdfium2-team/pypdfium2)）裁出图，[MinerU](https://github.com/opendatalab/MinerU) 作为 PDF 引擎转换 PDF，[mammoth](https://github.com/mwilliamson/python-mammoth) 读取 Word 文件，[PDF.js](https://github.com/mozilla/pdf.js) 显示 PDF，[KaTeX](https://katex.org) 和 [Latin Modern Math](https://www.gust.org.pl/projects/e-foundry/lm-math) 排版公式。感谢它们的作者。</sub>

[Apache 2.0](https://github.com/chenxiachan/textfold/blob/main/LICENSE) © 2026 Xia Chen · [开发文档](https://github.com/chenxiachan/textfold/blob/main/docs/DEVELOPMENT.md) · [格式](https://github.com/chenxiachan/textfold/blob/main/docs/format.md) · [参与贡献](https://github.com/chenxiachan/textfold/blob/main/CONTRIBUTING.md) · [安全](https://github.com/chenxiachan/textfold/blob/main/SECURITY.md) · [反馈](https://github.com/chenxiachan/textfold/issues)

</div>
