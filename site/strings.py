# The download page's words, in English and Chinese: one key, both languages. build.py puts them in template.html.
# Every claim here is the README's or the app's own (versions, systems, file names); keep them true when either changes.
# Few words: what the page must say and nothing the README says better (it links there).

VERSION = "0.10.54"
SITE = "https://chenxiachan.github.io/textfold/"
REPO = "https://github.com/chenxiachan/textfold"
DL = REPO + "/releases/latest/download/"
FILES = {"mac_arm": "TextFold-mac-arm64.dmg", "mac_x64": "TextFold-mac-x64.dmg", "win": "TextFold-win-x64-setup.exe"}
STORE = "https://chromewebstore.google.com/detail/textfold/lkfemlafmabkdjedldmjebdpccdaeglf"   # the browser's button
LANGS_NATIVE = "English, 简体中文, 繁體中文, 日本語, 한국어, Español, Français, Deutsch, Português, Italiano, Русский, हिन्दी, Svenska, Dansk, Norsk bokmål, Suomi, Nederlands, Polski, Čeština, Українська, Türkçe, Bahasa Indonesia, Tiếng Việt, ภาษาไทย"   # textfold/langs.py's, by their own names (tests/test_languages.py)

S = {
    # the head (search engines, AI answers, shared links)
    "title": ("TextFold · Fold any text, from a map to every word",
              "TextFold · 任何文字，从地图折到每一个字"),
    "description": ("TextFold is a free, open-source reader and toolkit that folds papers, web pages, documents and AI output into five levels, "
                    "from a one-screen map to every word. A Python package for your pipeline and agents, and an app for macOS and Windows.",
                    "TextFold 是免费开源的阅读器和工具包，把论文、网页、文档和 AI 的输出折成五层，从一屏地图到每一个字。"
                    "既是给流程和 Agent 用的 Python 包，也有 macOS 和 Windows 软件。"),

    "nav_download": ('Download',
        '下载'),
    "nav_gallery": ('Gallery',
        '在线试用'),
    "nav_pipe": ('Pipeline',
        '接入流程'),
    "nav_docs": ('Docs',
        '文档'),
    "nav_ext": ('Browser',
        '浏览器插件'),
    "lang_other": ('中文',
        'English'),

    "h1": ("Fold any text, from a one‑screen map to every word.", "任何文字，从一屏地图折到每一个字。"),
    "sub": ("Papers, web pages, documents, and your agent's output. On your computer.",
            "论文、网页、文档，还有 Agent 的输出。都在你自己的电脑上。"),
    "cta_mac": ('Download for Mac',
        '下载 Mac 版'),
    "cta_mac_x64": ('Download for Mac',
        '下载 Mac 版'),
    "cta_win": ('Download for Windows',
        '下载 Windows 版'),
    "copy": ('Copy',
        '复制'),
    "copied": ('Copied',
        '已复制'),

    # the paragraph that folds (demo.json: a real paper, its real levels)




    "pipe_h": ('Embed it in your pipeline',
        '嵌进你的流程'),
    "pipe_p": ("A Python package and a command: any document, one level at a time, coarsest first, in an open format. Any system with Python 3.9+.",
               "一个 Python 包和一条命令：任何文档都能按层取，从最粗的一层开始，用公开的格式。任何装有 Python 3.9+ 的系统都能用。"),
    "pipe_links": ('<a href="https://github.com/chenxiachan/textfold/blob/main/docs/format.md">The TextFold format</a> · <a href="https://github.com/chenxiachan/textfold#for-agents-and-scripts">For agents and scripts</a>',
        '<a href="https://github.com/chenxiachan/textfold/blob/main/docs/format.md">TextFold 格式</a> · <a href="https://github.com/chenxiachan/textfold/blob/main/README.zh-CN.md">给 Agent 和脚本</a>'),
    "agent_h": ('In your agent',
        '放进你的 Agent'),
    "agent_p": ('In Claude Code, every reply gets five levels, one key each, with no second model.',
        '在 Claude Code 里，每条回复都有五层，一个键一层，不用第二个模型。'),

    # the browser's button (README: Works in your browser; its store listing)
    "hero_ext": ('New: the TextFold button for Chrome →',
        '新：Chrome 上的 TextFold 按钮 →'),
    "ext_h": ('In your browser',
        '在浏览器里用'),
    "ext_p": ("One click sends the page you are reading to TextFold, as you see it: signed in, past a paywall you pay for, drawn by its scripts. With TextFold open, the page opens folded beside it.",
              "点一下，就把正在读的网页按你看到的样子交给 TextFold：登录后的、你订阅的、由脚本画出来的内容都一样。TextFold 开着时，折好的页面在网页旁边打开。"),
    "ext_for": ('For Chrome, Brave and Edge, from the Chrome Web Store.',
        '适用于 Chrome、Brave 和 Edge，从 Chrome 网上应用店安装。'),
    "ext_btn": ('Add to Chrome',
        '添加到 Chrome'),
    "ext_fine": ('It hands the page to TextFold on this computer and to nothing else (<a href="privacy.html">privacy</a>). It needs the TextFold app above, or the Python package and <code>python3 -m textfold capture install</code>, once.',
        '它只把网页交给本机的 TextFold，不交给任何别处（<a href="privacy.html#zh">隐私政策</a>）。需要先装好上面的 TextFold 软件，或者 Python 包再运行一次 <code>python3 -m textfold capture install</code>。'),
    "ext_cap": ('A Wikipedia article, sent to TextFold with one click, then folded level by level.',
        '一篇维基百科文章，点一下传进 TextFold，再一层层折叠。'),

    "app_p": ('Nothing else to install. It finds Claude Code, Codex, Pi, WorkBuddy or Antigravity on your computer, or connects a free model in one click.',
        '不用再装别的。它会找到你电脑上的 Claude Code、Codex、Pi、WorkBuddy 或 Antigravity，也能一键连上免费模型。'),

    "which_mac": ('<b>Which Mac?</b> Apple menu › About This Mac: <em>Chip Apple M…</em> or <em>Processor Intel</em>.',
        '<b>哪种 Mac？</b>苹果菜单 › 关于本机：写着 <em>芯片 Apple M…</em> 或 <em>处理器 Intel</em>。'),
    "app_meta": (f'Version {VERSION} · macOS 12 or later · The app updates itself.',
        f'版本 {VERSION} · macOS 12 或更新 · 软件会自己更新。'),
    "for_you": ('For this computer',
        '适合这台电脑'),

    "faq_h": ('Questions',
        '常见问题'),
    "q1": ("What is TextFold?", "TextFold 是什么？"),
    "a1": ("A free, open-source reader and toolkit that folds any text into five levels, from a map of its sections to every word.",
           "免费开源的阅读器和工具包，把任何文字折成五层，从章节地图到每一个字。"),
    "q2": ("Is it free?", "免费吗？"),
    "a2": ("Yes, under the Apache License 2.0. The levels are written by a model you connect, so any cost is that model's.",
           "免费，Apache 2.0 许可证。各层由你连接的模型来写，如果有费用，也是那个模型的。"),
    "q3": ("How is it different from a summary?", "和摘要有什么不同？"),
    "a3": ("The text stays. The two finest levels are the author's own sentences, and every line a model writes points back to them.",
           "原文一直都在。最细的两层就是作者的原句，模型写的每一行都指回这些原句。"),
    "q4": ("Does it upload my files?", "会上传我的文件吗？"),
    "a4": ("No. Your library stays on your computer, and text goes only to the model you connect.",
           "不会。文献库留在你自己的电脑上，文字只发给你连接的模型。"),
    "q5": ("Can AI agents use it?", "AI Agent 能用吗？"),
    "a5": ("Yes. <code>textfold get</code> gives an agent a document one level at a time, coarsest first, and an open format lets any program "
           "read and write folded text.",
           "能。<code>textfold get</code> 按层把文档交给 Agent，从最粗的一层开始；公开的格式让任何程序都能读写折好的文字。"),

    "foot_readme": ('README',
        'README'),
    "foot_format": ('The format',
        '格式'),
    "foot_releases": ('Releases',
        '所有版本'),
    "foot_privacy": ('Privacy',
        '隐私'),
    "foot_demo": ('Demo gallery',
        '在线试用'),
    "notes": ('Release notes',
        '更新说明'),
    "cta_py": ('Embed it in your pipeline',
        '嵌进你的流程'),
    "foot_license": ('Apache 2.0 © 2026 Xia Chen',
        'Apache 2.0 © 2026 Xia Chen'),

    "hero_note": ('Free and open source · No account · Formerly PaperFold',
        '免费开源 · 不用注册 · 原名 PaperFold'),
    "cta_mac_sub": ('Apple silicon · .dmg',
        'Apple 芯片 · .dmg'),
    "cta_mac_x64_sub": ('Intel · .dmg',
        'Intel · .dmg'),
    "cta_win_sub": ('64-bit · installer',
        '64 位 · 安装程序'),
    "cta_py_sub": ('python3 -m pip install textfold',
        'python3 -m pip install textfold'),

    "video_cap": ('Five levels of one paper, in the app.',
        '一篇论文的五个层级，在软件里切换。'),

    "dl_h": ('Get TextFold',
        '获取 TextFold'),
    "apps_h": ('Or read in the app',
        '或者，用软件来读'),
    "dl_mac_arm_os": ('macOS · Apple silicon',
        'macOS · Apple 芯片'),
    "dl_mac_arm_for": ('Macs with an M1 chip or later',
        'M1 及之后芯片的 Mac'),
    "dl_mac_x64_os": ('macOS · Intel',
        'macOS · Intel'),
    "dl_mac_x64_for": ('Macs with an Intel processor',
        'Intel 处理器的 Mac'),
    "dl_mac_signed": ('Signed and notarized by Apple',
        '已签名，并经苹果公证'),
    "dl_win_os": ('Windows',
        'Windows'),
    "dl_win_for": ('Windows 10 and 11, 64-bit',
        'Windows 10 和 11，64 位'),
    "dl_win_note": ('Not signed yet: the first time, choose More info, then Run anyway.',
        '暂未签名：第一次打开时点“更多信息”，再点“仍要运行”。'),
    "dl_btn_dmg": ('Download .dmg',
        '下载 .dmg'),
    "dl_btn_exe": ('Download installer',
        '下载安装程序'),
    "see_h": ('See it work',
        '看它怎么用'),
    "clip02": ('Read beside the original PDF',
        '原文 PDF 就在旁边'),
    "clip04": ('Keep the passages you marked',
        '标过的地方，折起来也在'),
    "clip03": ('Your text and interface, in your language',
        '正文和界面，一起换语言'),
    "clip06": ('Read a Word document at different levels',
        'Word 文档也能分层读'),
    "credit": ('The recordings read <a href="https://arxiv.org/abs/2512.23916">Constraint Breeds Generalization</a> (Xia Chen, arXiv:2512.23916); the Word document was written for the demo.',
        '录屏读的是 <a href="https://arxiv.org/abs/2512.23916">Constraint Breeds Generalization</a>（Xia Chen，arXiv:2512.23916）；Word 文档是专为演示写的。'),
    "readme_line": ('The <a href="https://github.com/chenxiachan/textfold#readme">README</a> has the rest: every source it reads, every key, every model.',
        '其余的都在 <a href="https://github.com/chenxiachan/textfold/blob/main/README.zh-CN.md">README</a> 里：能读的所有来源、所有快捷键、所有模型。'),
    # the opening's typed line (ascii.rest's typewriter): what can be folded
    "typer_prefix": ("fold ", "折叠"),
    "logo_alt": ("Three paragraphs folding to their points, fold any text, keep the point, unfold, the rest gathered into the bars of TextFold's mark, and opening out again",
        "三段文字折到各自的要点（fold any text、keep the point、unfold），其余收进 TextFold 标志的三条横条，再展开回来"),
}

FAQ = [(f"q{i}", f"a{i}") for i in range(1, 6)]
TYPED = {"en": ["a paper.", "a web page.", "a PDF.", "a Word file.", "your agent's output."],
         "zh": ["一篇论文。", "一个网页。", "一份 PDF。", "一份 Word 文档。", "Agent 的输出。"]}
