// How TextFold works: a few pages of small pictures, two or three ideas a page. It opens by itself on a first visit to
// the landing, and from "How it works" there and in the reader: window.TFTutorial.open(lang). Its words live here, not in
// i18n.js, so that a page exported on its own (which carries i18n.js) does not carry them too.
(() => {
  'use strict';
  const KEY = 'textfold.tutorialDone';
  const store = { get(k) { try { return localStorage.getItem(k); } catch { return null; } }, set(k, v) { try { localStorage.setItem(k, v); } catch { /* private window */ } } };
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  // the pages, and the ideas on each
  const PAGES = [['link', 'file', 'lang'], ['levels', 'map', 'zoom'], ['voice', 'anchor'], ['original', 'ask', 'notes']];

  // {levels}, {topic}, {brief} and {original} are the reader's own names for them (i18n.js), filled in when shown
  const W = {
    en: {
      title: 'How TextFold works', sub: 'Four short pages: how to read a text that folds.',
      p1: 'Bring a text in', p2: 'Fold and unfold', p3: 'Whose words', p4: 'Read deeper',
      link_t: 'Paste a link', link_d: 'Any web page works. A paper’s link or DOI, Wikipedia and GitHub are read with their sections, formulas and figures.',
      file_t: 'Or drop a file', file_d: 'A PDF, a Word, PowerPoint or Excel file, an EPUB, a picture, Markdown or a .txt. A PDF that names a paper online opens that version. Other PDFs and files are converted on your computer.',
      lang_t: 'Read in your language', lang_d: 'Pick from many languages. The text is translated sentence by sentence, and every formula stays as typeset.',
      levels_t: 'Five levels', levels_d: '{levels}. Each level keeps less of every paragraph, so more of the whole fits on the screen.',
      map_t: 'A map of the text', map_d: 'At {topic}, the text becomes a map of section cards. You see how the sections build on each other before you read any of them.',
      zoom_t: 'Zoom under your fingers', zoom_d: 'Pinch, scroll with ⌘ or Ctrl, press 1 to 5, or drag the level bar. Words that stay slide to their new place. Click a paragraph to open just that one.',
      voice_t: 'Serif is the author', voice_d: 'The author’s words are set in a serif, AI’s in a sans serif. {brief} keeps the author’s own sentences, word for word.',
      anchor_t: 'AI points to its source', anchor_d: 'Every line AI wrote points back to the sentences it rests on. Colors mark each paragraph’s role in the argument.',
      original_t: 'Beside the original', original_d: 'For a paper or a PDF, {original} at the top opens its pages at the left. Each paragraph is marked where it stands on its page.',
      ask_t: 'Ask, and see the evidence', ask_d: 'Select a passage to ask about it, or ask about the whole text. Every answer cites the paragraphs it rests on, one click away.',
      notes_t: 'Notes that zoom with you', notes_d: 'Highlight and write in the margin. What you mark stays as you zoom out. Save keeps the page with them as one file, or only your notes.',
      done: 'Got it', prev: 'Previous page', next: 'Next page', close: 'Close',
      k_levels: 'levels', k_scroll: 'scroll', k_zoom: 'zoom', k_lang: 'next language', k_pages: 'pages', k_close: 'close',
    },
    zh: {
      title: 'TextFold 怎么用', sub: '四页，三分钟，看懂怎样把一份文本折起来读。',
      p1: '带一份文本进来', p2: '折叠与展开', p3: '谁在说话', p4: '读得更深',
      link_t: '粘贴链接', link_d: '任意网页都可以。论文链接或 DOI、维基百科、GitHub 仓库，会连同章节、公式和图一起读进来。',
      file_t: '或者拖入文件', file_d: 'PDF、Word、PowerPoint、Excel、EPUB、图片、Markdown 或 .txt 文件。PDF 如果对应网上的某篇论文，就打开网上的版本。其他 PDF 和文件在你的电脑上转换。',
      lang_t: '用你的语言读', lang_d: '多种语言任选。逐句翻译，公式保持原样。',
      levels_t: '五个层级', levels_d: '{levels}。每往上一级，每段留下的更少，一屏能看到的整体就更多。',
      map_t: '一张文本地图', map_d: '到了{topic}层，文本变成一张章节卡片的地图。还没读正文，就先看清各章节怎样一步步搭起来。',
      zoom_t: '随手缩放', zoom_d: '双指捏合、按住 ⌘ 或 Ctrl 滚动、按数字键 1 到 5，或拖动层级条。留下来的词会滑到新位置。点一个段落，只展开这一段。',
      voice_t: '衬线体是作者的原话', voice_d: '作者的原话用衬线体，AI 写的用无衬线体。{brief}层保留的是作者的原句，一字不改。',
      anchor_t: 'AI 的每句话都有出处', anchor_d: 'AI 写的每一行都指回它依据的原句。颜色标出每段在论证中的角色。',
      original_t: '对照原文', original_d: '论文和 PDF 可以点顶部的「{original}」，左边打开原来的页面。每一段都标出它在页上的位置。',
      ask_t: '提问，并看到依据', ask_d: '选中一段文字就能就它提问，也可以就全文提问。每个回答都标出依据的段落，点一下就能跳过去。',
      notes_t: '笔记跟着缩放', notes_d: '高亮，在页边写笔记。缩小时，你标过的内容会留下来。用「保存」把整页连同笔记存成一个文件，或者只存笔记。',
      done: '明白了', prev: '上一页', next: '下一页', close: '关闭',
      k_levels: '层级', k_scroll: '滚动', k_zoom: '缩放', k_lang: '切换语言', k_pages: '翻页', k_close: '关闭',
    },
    'zh-Hant': {
      title: 'TextFold 怎麼用', sub: '四頁，三分鐘，看懂怎樣把一份文本折起來讀。',
      p1: '帶一份文本進來', p2: '折疊與展開', p3: '誰在說話', p4: '讀得更深',
      link_t: '貼上連結', link_d: '任何網頁都可以。論文連結或 DOI、維基百科、GitHub 儲存庫，會連同章節、公式和圖一起讀進來。',
      file_t: '或者拖入檔案', file_d: 'PDF、Word、PowerPoint、Excel、EPUB、圖片、Markdown 或 .txt 檔案。PDF 如果對應網路上的某篇論文，就開啟網路上的版本。其他 PDF 和檔案在你的電腦上轉換。',
      lang_t: '用你的語言讀', lang_d: '多種語言任選。逐句翻譯，公式保持原樣。',
      levels_t: '五個層級', levels_d: '{levels}。每往上一級，每段留下的更少，一個畫面能看到的整體就更多。',
      map_t: '一張文本地圖', map_d: '到了{topic}層，文本變成一張章節卡片的地圖。還沒讀內文，就先看清各章節怎樣一步步搭起來。',
      zoom_t: '隨手縮放', zoom_d: '雙指捏合、按住 ⌘ 或 Ctrl 捲動、按數字鍵 1 到 5，或拖動層級條。留下來的詞會滑到新位置。點一個段落，只展開這一段。',
      voice_t: '襯線體是作者的原話', voice_d: '作者的原話用襯線體，AI 寫的用無襯線體。{brief}層保留的是作者的原句，一字不改。',
      anchor_t: 'AI 的每句話都有出處', anchor_d: 'AI 寫的每一行都指回它依據的原句。顏色標出每段在論證中的角色。',
      original_t: '對照原文', original_d: '論文和 PDF 可以點頂部的「{original}」，左邊開啟原來的頁面。每一段都標出它在頁上的位置。',
      ask_t: '提問，並看到依據', ask_d: '選取一段文字就能針對它提問，也可以針對全文提問。每個回答都標出依據的段落，點一下就能跳過去。',
      notes_t: '筆記跟著縮放', notes_d: '標示重點，在頁邊寫筆記。縮小時，你標過的內容會留下來。用「儲存」把整頁連同筆記存成一個檔案，或者只存筆記。',
      done: '了解了', prev: '上一頁', next: '下一頁', close: '關閉',
      k_levels: '層級', k_scroll: '捲動', k_zoom: '縮放', k_lang: '切換語言', k_pages: '翻頁', k_close: '關閉',
    },
    ja: {
      title: 'TextFold の使い方', sub: '4 ページ、3 分で、テキストを折りたたんで読む方法がわかります。',
      p1: 'テキストを取り込む', p2: '折りたたむ、広げる', p3: '誰の言葉か', p4: 'さらに深く読む',
      link_t: 'リンクを貼り付ける', link_d: 'どんなウェブページでも読めます。論文のリンクや DOI、ウィキペディア、GitHub は、章立てや数式、図もそのまま取り込みます。',
      file_t: 'ファイルをドロップ', file_d: 'PDF、Word、PowerPoint、Excel、EPUB、画像、Markdown、.txt のファイルを開けます。オンラインの論文に対応する PDF はその版を開きます。それ以外の PDF やファイルはお使いのコンピューター上で変換します。',
      lang_t: 'あなたの言語で読む', lang_d: '多くの言語から選べます。一文ずつ翻訳し、数式は組まれたままです。',
      levels_t: '5 つのレベル', levels_d: '{levels}。レベルが上がるほど各段落は短くなり、全体が一画面に収まります。',
      map_t: 'テキストの地図', map_d: '{topic}では、テキストが章ごとのカードの地図になります。本文を読む前に、章どうしのつながりが見えます。',
      zoom_t: '指先でズーム', zoom_d: 'ピンチ、⌘ か Ctrl を押しながらスクロール、1〜5 のキー、またはレベルバーのドラッグで切り替えます。残る言葉は新しい位置へ滑っていきます。段落をクリックすると、その段落だけを開きます。',
      voice_t: 'セリフ体は著者の言葉', voice_d: '著者の言葉はセリフ体、AI が書いた文はサンセリフ体で表示します。{brief}は著者の文を一字一句そのまま残します。',
      anchor_t: 'AI の文には出典がある', anchor_d: 'AI が書いた各行は、根拠となった原文の文を指し示します。段落の色は、論証の中での役割を表します。',
      original_t: '原文と並べて読む', original_d: '論文や PDF では、上部の「{original}」で左側に元のページを開けます。各段落がページのどこにあるかを示します。',
      ask_t: '質問して、根拠を確かめる', ask_d: '文章を選んでその部分について質問することも、全文について質問することもできます。どの回答も根拠の段落を示し、クリックひとつで移動できます。',
      notes_t: 'ズームしても残るメモ', notes_d: 'ハイライトして余白にメモを書けます。縮小しても、印を付けた部分は残ります。「保存」で、メモごとページを 1 つのファイルに、またはメモだけを保存できます。',
      done: 'わかりました', prev: '前のページ', next: '次のページ', close: '閉じる',
      k_levels: 'レベル', k_scroll: 'スクロール', k_zoom: 'ズーム', k_lang: '言語を切り替え', k_pages: 'ページ送り', k_close: '閉じる',
    },
    ko: {
      title: 'TextFold 사용법', sub: '네 쪽, 3분이면 글을 접어서 읽는 법을 알 수 있습니다.',
      p1: '글 가져오기', p2: '접고 펼치기', p3: '누구의 말인가', p4: '더 깊이 읽기',
      link_t: '링크 붙여넣기', link_d: '어떤 웹 페이지든 읽을 수 있습니다. 논문 링크나 DOI, 위키백과, GitHub는 장 구성과 수식, 그림까지 그대로 가져옵니다.',
      file_t: '파일 끌어다 놓기', file_d: 'PDF, Word, PowerPoint, Excel, EPUB, 이미지, Markdown, .txt 파일을 열 수 있습니다. 온라인 논문과 맞는 PDF는 그 판을 엽니다. 다른 PDF와 파일은 이 컴퓨터에서 변환합니다.',
      lang_t: '내 언어로 읽기', lang_d: '여러 언어 중에서 고를 수 있습니다. 문장 단위로 번역하고, 수식은 그대로 둡니다.',
      levels_t: '다섯 단계', levels_d: '{levels}. 단계가 올라갈수록 단락은 짧아지고, 한 화면에 전체가 더 많이 들어옵니다.',
      map_t: '글의 지도', map_d: '{topic} 단계에서는 글이 장별 카드로 된 지도가 됩니다. 본문을 읽기 전에 장들이 어떻게 이어지는지 보입니다.',
      zoom_t: '손끝으로 확대와 축소', zoom_d: '핀치하거나, ⌘ 또는 Ctrl을 누른 채 스크롤하거나, 숫자 키 1~5를 누르거나, 단계 막대를 끌어 보세요. 남는 단어는 새 자리로 미끄러져 갑니다. 단락을 클릭하면 그 단락만 펼쳐집니다.',
      voice_t: '세리프체는 저자의 말', voice_d: '저자의 말은 세리프체로, AI가 쓴 글은 산세리프체로 보입니다. {brief} 단계는 저자의 문장을 한 글자도 바꾸지 않고 그대로 둡니다.',
      anchor_t: 'AI의 문장에는 출처가 있습니다', anchor_d: 'AI가 쓴 모든 줄은 근거가 된 원문 문장을 가리킵니다. 단락의 색은 논증에서 맡은 역할을 나타냅니다.',
      original_t: '원문과 나란히', original_d: '논문이나 PDF는 위쪽의 \'{original}\' 버튼을 누르면 왼쪽에 원래 페이지가 열립니다. 각 단락이 페이지의 어디에 있는지 표시됩니다.',
      ask_t: '질문하고 근거 확인하기', ask_d: '글의 일부를 선택해 그 부분에 대해 묻거나, 글 전체에 대해 물을 수 있습니다. 모든 답은 근거가 된 단락을 보여 주며, 클릭 한 번으로 이동합니다.',
      notes_t: '확대·축소해도 남는 메모', notes_d: '형광펜으로 표시하고 여백에 메모하세요. 축소해도 표시한 부분은 남습니다. ‘저장’으로 메모와 함께 페이지를 파일 하나로, 또는 메모만 저장할 수 있습니다.',
      done: '알겠습니다', prev: '이전 쪽', next: '다음 쪽', close: '닫기',
      k_levels: '단계', k_scroll: '스크롤', k_zoom: '확대·축소', k_lang: '언어 바꾸기', k_pages: '쪽 넘기기', k_close: '닫기',
    },
    es: {
      title: 'Cómo funciona TextFold', sub: 'Cuatro páginas breves: cómo leer un texto que se pliega.',
      p1: 'Trae un texto', p2: 'Plegar y desplegar', p3: 'De quién son las palabras', p4: 'Leer más a fondo',
      link_t: 'Pega un enlace', link_d: 'Sirve cualquier página web. El enlace o el DOI de un artículo, Wikipedia y GitHub se leen con sus secciones, fórmulas y figuras.',
      file_t: 'O suelta un archivo', file_d: 'Un PDF, un archivo de Word, PowerPoint o Excel, un EPUB, una imagen, Markdown o un .txt. Si el PDF corresponde a un artículo en línea, se abre esa versión. Los demás PDF y archivos se convierten en tu ordenador.',
      lang_t: 'Lee en tu idioma', lang_d: 'Elige entre muchos idiomas. El texto se traduce frase a frase y cada fórmula queda tal como está.',
      levels_t: 'Cinco niveles', levels_d: '{levels}. Cada nivel conserva menos de cada párrafo, así que cabe más del conjunto en la pantalla.',
      map_t: 'Un mapa del texto', map_d: 'En el nivel {topic}, el texto se convierte en un mapa de tarjetas por sección. Ves cómo se apoyan unas secciones en otras antes de leer ninguna.',
      zoom_t: 'Zoom con los dedos', zoom_d: 'Pellizca, desplaza con ⌘ o Ctrl, pulsa de 1 a 5 o arrastra la barra de niveles. Las palabras que se quedan se deslizan a su nuevo sitio. Haz clic en un párrafo para abrir solo ese.',
      voice_t: 'La serifa es el autor', voice_d: 'Las palabras del autor van en una letra con serifa y las de la IA en una sin serifa. {brief} conserva las frases del autor, palabra por palabra.',
      anchor_t: 'La IA señala su fuente', anchor_d: 'Cada línea que escribe la IA apunta a las frases en que se apoya. Los colores marcan el papel de cada párrafo en el argumento.',
      original_t: 'Junto al original', original_d: 'En un artículo o un PDF, «{original}», arriba, abre sus páginas a la izquierda. Cada párrafo aparece marcado donde está en su página.',
      ask_t: 'Pregunta y ve la evidencia', ask_d: 'Selecciona un pasaje para preguntar sobre él, o pregunta sobre todo el texto. Cada respuesta cita los párrafos en que se apoya, a un clic.',
      notes_t: 'Notas que siguen el zoom', notes_d: 'Resalta y escribe en el margen. Lo que marcas se queda al alejar el zoom. «Guardar» guarda la página con tus notas en un archivo, o solo tus notas.',
      done: 'Entendido', prev: 'Página anterior', next: 'Página siguiente', close: 'Cerrar',
      k_levels: 'niveles', k_scroll: 'rueda', k_zoom: 'zoom', k_lang: 'siguiente idioma', k_pages: 'páginas', k_close: 'cerrar',
    },
    fr: {
      title: 'Comment fonctionne TextFold', sub: 'Quatre pages courtes : lire un texte qui se plie.',
      p1: 'Apporter un texte', p2: 'Plier et déplier', p3: 'Qui parle', p4: 'Lire plus loin',
      link_t: 'Collez un lien', link_d: 'N’importe quelle page web convient. Le lien ou le DOI d’un article, Wikipédia et GitHub sont lus avec leurs sections, leurs formules et leurs figures.',
      file_t: 'Ou déposez un fichier', file_d: 'Un PDF, un fichier Word, PowerPoint ou Excel, un EPUB, une image, du Markdown ou un .txt. Si le PDF correspond à un article en ligne, c’est cette version qui s’ouvre. Les autres PDF et fichiers sont convertis sur votre ordinateur.',
      lang_t: 'Lisez dans votre langue', lang_d: 'Choisissez parmi de nombreuses langues. Le texte est traduit phrase par phrase, et chaque formule reste telle quelle.',
      levels_t: 'Cinq niveaux', levels_d: '{levels}. Chaque niveau garde moins de chaque paragraphe, et l’ensemble tient mieux sur un écran.',
      map_t: 'Une carte du texte', map_d: 'Au niveau {topic}, le texte devient une carte de fiches par section. Vous voyez comment les sections s’enchaînent avant d’en lire une seule.',
      zoom_t: 'Zoomer du bout des doigts', zoom_d: 'Pincez, faites défiler avec ⌘ ou Ctrl, tapez 1 à 5 ou faites glisser la barre des niveaux. Les mots qui restent glissent vers leur nouvelle place. Cliquez sur un paragraphe pour n’ouvrir que lui.',
      voice_t: 'L’empattement, c’est l’auteur', voice_d: 'Les mots de l’auteur sont en caractères à empattement, ceux de l’IA sans empattement. {brief} garde les phrases de l’auteur, mot pour mot.',
      anchor_t: 'L’IA montre sa source', anchor_d: 'Chaque ligne écrite par l’IA renvoie aux phrases sur lesquelles elle s’appuie. Les couleurs indiquent le rôle de chaque paragraphe dans l’argument.',
      original_t: 'À côté de l’original', original_d: 'Pour un article ou un PDF, « {original} », en haut, ouvre ses pages à gauche. Chaque paragraphe y est marqué à sa place sur la page.',
      ask_t: 'Demandez, et voyez les preuves', ask_d: 'Sélectionnez un passage pour poser une question dessus, ou interrogez tout le texte. Chaque réponse cite les paragraphes sur lesquels elle s’appuie, à un clic.',
      notes_t: 'Des notes qui suivent le zoom', notes_d: 'Surlignez et écrivez dans la marge. Ce que vous marquez reste quand vous dézoomez. « Enregistrer » garde la page avec vos notes dans un fichier, ou seulement vos notes.',
      done: 'Compris', prev: 'Page précédente', next: 'Page suivante', close: 'Fermer',
      k_levels: 'niveaux', k_scroll: 'défilement', k_zoom: 'zoom', k_lang: 'langue suivante', k_pages: 'pages', k_close: 'fermer',
    },
    de: {
      title: 'So funktioniert TextFold', sub: 'Vier kurze Seiten: wie man einen Text liest, der sich falten lässt.',
      p1: 'Einen Text hereinholen', p2: 'Falten und entfalten', p3: 'Wessen Worte', p4: 'Tiefer lesen',
      link_t: 'Einen Link einfügen', link_d: 'Jede Webseite geht. Der Link oder die DOI eines Papers, Wikipedia und GitHub werden mit Abschnitten, Formeln und Abbildungen gelesen.',
      file_t: 'Oder eine Datei ablegen', file_d: 'Eine PDF, eine Word-, PowerPoint- oder Excel-Datei, ein EPUB, ein Bild, Markdown oder eine .txt. Gehört die PDF zu einem Paper im Netz, öffnet sich diese Fassung. Andere PDFs und Dateien werden auf deinem Computer umgewandelt.',
      lang_t: 'In deiner Sprache lesen', lang_d: 'Wähle aus vielen Sprachen. Der Text wird Satz für Satz übersetzt, und jede Formel bleibt, wie sie gesetzt ist.',
      levels_t: 'Fünf Stufen', levels_d: '{levels}. Jede Stufe behält weniger von jedem Absatz, so passt mehr vom Ganzen auf den Bildschirm.',
      map_t: 'Eine Karte des Textes', map_d: 'Auf der Stufe {topic} wird der Text zu einer Karte aus Abschnittskarten. Du siehst, wie die Abschnitte aufeinander aufbauen, bevor du einen davon liest.',
      zoom_t: 'Zoomen mit den Fingern', zoom_d: 'Mit zwei Fingern ziehen, mit ⌘ oder Strg scrollen, die Tasten 1 bis 5 drücken oder die Stufenleiste ziehen. Wörter, die bleiben, gleiten an ihren neuen Platz. Ein Klick auf einen Absatz öffnet nur diesen.',
      voice_t: 'Serifen sind der Autor', voice_d: 'Die Worte des Autors stehen in einer Serifenschrift, die der KI in einer serifenlosen. {brief} behält die Sätze des Autors Wort für Wort.',
      anchor_t: 'Die KI zeigt ihre Quelle', anchor_d: 'Jede Zeile, die die KI schreibt, verweist auf die Sätze, auf die sie sich stützt. Farben zeigen die Rolle jedes Absatzes im Argument.',
      original_t: 'Neben dem Original', original_d: 'Bei einem Paper oder einer PDF öffnet „{original}“ oben die Seiten links. Jeder Absatz ist dort markiert, wo er auf seiner Seite steht.',
      ask_t: 'Fragen und die Belege sehen', ask_d: 'Markiere eine Stelle, um danach zu fragen, oder frage den ganzen Text. Jede Antwort nennt die Absätze, auf die sie sich stützt, einen Klick entfernt.',
      notes_t: 'Notizen, die mitzoomen', notes_d: 'Markiere und schreibe an den Rand. Was du markierst, bleibt beim Herauszoomen stehen. „Speichern“ legt die Seite mit deinen Notizen als eine Datei ab, oder nur deine Notizen.',
      done: 'Verstanden', prev: 'Vorherige Seite', next: 'Nächste Seite', close: 'Schließen',
      k_levels: 'Stufen', k_scroll: 'Scrollen', k_zoom: 'Zoom', k_lang: 'nächste Sprache', k_pages: 'Seiten', k_close: 'schließen',
    },
    pt: {
      title: 'Como o TextFold funciona', sub: 'Quatro páginas curtas: como ler um texto que se dobra.',
      p1: 'Traga um texto', p2: 'Dobrar e desdobrar', p3: 'De quem são as palavras', p4: 'Ler mais a fundo',
      link_t: 'Cole um link', link_d: 'Qualquer página web serve. O link ou o DOI de um artigo, a Wikipédia e o GitHub são lidos com suas seções, fórmulas e figuras.',
      file_t: 'Ou solte um arquivo', file_d: 'Um PDF, um arquivo do Word, PowerPoint ou Excel, um EPUB, uma imagem, Markdown ou um .txt. Se o PDF corresponde a um artigo on-line, abre essa versão. Os outros PDFs e arquivos são convertidos no seu computador.',
      lang_t: 'Leia no seu idioma', lang_d: 'Escolha entre muitos idiomas. O texto é traduzido frase por frase, e cada fórmula fica como foi composta.',
      levels_t: 'Cinco níveis', levels_d: '{levels}. Cada nível guarda menos de cada parágrafo, então cabe mais do todo na tela.',
      map_t: 'Um mapa do texto', map_d: 'No nível {topic}, o texto vira um mapa de cartões por seção. Você vê como as seções se apoiam umas nas outras antes de ler qualquer uma.',
      zoom_t: 'Zoom na ponta dos dedos', zoom_d: 'Faça pinça, role com ⌘ ou Ctrl, aperte de 1 a 5 ou arraste a barra de níveis. As palavras que ficam deslizam para o novo lugar. Clique em um parágrafo para abrir só ele.',
      voice_t: 'Serifa é o autor', voice_d: 'As palavras do autor aparecem em fonte serifada, as da IA em fonte sem serifa. {brief} guarda as frases do autor, palavra por palavra.',
      anchor_t: 'A IA mostra sua fonte', anchor_d: 'Cada linha escrita pela IA aponta para as frases em que se apoia. As cores marcam o papel de cada parágrafo no argumento.',
      original_t: 'Ao lado do original', original_d: 'Em um artigo ou PDF, “{original}”, no topo, abre as páginas à esquerda. Cada parágrafo aparece marcado onde está na página.',
      ask_t: 'Pergunte e veja as evidências', ask_d: 'Selecione um trecho para perguntar sobre ele, ou pergunte sobre o texto todo. Cada resposta cita os parágrafos em que se apoia, a um clique.',
      notes_t: 'Notas que acompanham o zoom', notes_d: 'Destaque e escreva na margem. O que você marca fica quando você afasta o zoom. “Salvar” guarda a página com suas notas em um arquivo, ou só as notas.',
      done: 'Entendi', prev: 'Página anterior', next: 'Próxima página', close: 'Fechar',
      k_levels: 'níveis', k_scroll: 'rolagem', k_zoom: 'zoom', k_lang: 'próximo idioma', k_pages: 'páginas', k_close: 'fechar',
    },
    it: {
      title: 'Come funziona TextFold', sub: 'Quattro pagine brevi: come leggere un testo che si piega.',
      p1: 'Porta un testo', p2: 'Piegare e aprire', p3: 'Di chi sono le parole', p4: 'Leggere più a fondo',
      link_t: 'Incolla un link', link_d: 'Va bene qualsiasi pagina web. Il link o il DOI di un articolo, Wikipedia e GitHub vengono letti con sezioni, formule e figure.',
      file_t: 'Oppure trascina un file', file_d: 'Un PDF, un file Word, PowerPoint o Excel, un EPUB, un’immagine, Markdown o un .txt. Se il PDF corrisponde a un articolo online, si apre quella versione. Gli altri PDF e file vengono convertiti sul tuo computer.',
      lang_t: 'Leggi nella tua lingua', lang_d: 'Scegli tra molte lingue. Il testo è tradotto frase per frase e ogni formula resta com’è.',
      levels_t: 'Cinque livelli', levels_d: '{levels}. Ogni livello tiene meno di ogni paragrafo, così sullo schermo entra più dell’insieme.',
      map_t: 'Una mappa del testo', map_d: 'Al livello {topic}, il testo diventa una mappa di schede per sezione. Vedi come le sezioni si reggono l’una sull’altra prima di leggerne una.',
      zoom_t: 'Zoom sotto le dita', zoom_d: 'Pizzica, scorri con ⌘ o Ctrl, premi da 1 a 5 o trascina la barra dei livelli. Le parole che restano scivolano al loro nuovo posto. Fai clic su un paragrafo per aprire solo quello.',
      voice_t: 'Le grazie sono l’autore', voice_d: 'Le parole dell’autore sono in un carattere con grazie, quelle dell’IA senza grazie. {brief} tiene le frasi dell’autore, parola per parola.',
      anchor_t: 'L’IA indica la fonte', anchor_d: 'Ogni riga scritta dall’IA rimanda alle frasi su cui si basa. I colori segnano il ruolo di ogni paragrafo nell’argomentazione.',
      original_t: 'Accanto all’originale', original_d: 'Per un articolo o un PDF, «{original}», in alto, apre le pagine a sinistra. Ogni paragrafo è segnato dove si trova nella sua pagina.',
      ask_t: 'Chiedi e vedi le prove', ask_d: 'Seleziona un passo per chiedere su quello, oppure chiedi su tutto il testo. Ogni risposta cita i paragrafi su cui si basa, a un clic.',
      notes_t: 'Note che seguono lo zoom', notes_d: 'Evidenzia e scrivi a margine. Ciò che segni resta quando riduci lo zoom. «Salva» conserva la pagina con le tue note in un file, o solo le note.',
      done: 'Ho capito', prev: 'Pagina precedente', next: 'Pagina successiva', close: 'Chiudi',
      k_levels: 'livelli', k_scroll: 'scorrimento', k_zoom: 'zoom', k_lang: 'lingua successiva', k_pages: 'pagine', k_close: 'chiudi',
    },
    ru: {
      title: 'Как работает TextFold', sub: 'Четыре короткие страницы: как читать текст, который складывается.',
      p1: 'Добавьте текст', p2: 'Сложить и развернуть', p3: 'Чьи это слова', p4: 'Читать глубже',
      link_t: 'Вставьте ссылку', link_d: 'Подойдёт любая веб-страница. Ссылка или DOI статьи, Википедия и GitHub читаются вместе с разделами, формулами и рисунками.',
      file_t: 'Или перетащите файл', file_d: 'PDF, файл Word, PowerPoint или Excel, EPUB, изображение, Markdown или .txt. Если PDF соответствует статье в сети, откроется эта версия. Остальные PDF и файлы преобразуются на вашем компьютере.',
      lang_t: 'Читайте на своём языке', lang_d: 'Выберите один из многих языков. Текст переводится по предложениям, а каждая формула остаётся как есть.',
      levels_t: 'Пять уровней', levels_d: '{levels}. На каждом уровне от абзаца остаётся меньше, и на экране помещается больше целого.',
      map_t: 'Карта текста', map_d: 'На уровне «{topic}» текст становится картой из карточек разделов. Видно, как разделы опираются друг на друга, ещё до чтения.',
      zoom_t: 'Масштаб под пальцами', zoom_d: 'Сведите пальцы, прокрутите с ⌘ или Ctrl, нажмите клавиши от 1 до 5 или потяните полосу уровней. Слова, которые остаются, переезжают на новое место. Щёлкните по абзацу, чтобы открыть только его.',
      voice_t: 'Засечки означают автора', voice_d: 'Слова автора набраны шрифтом с засечками, а слова ИИ набраны шрифтом без засечек. Уровень «{brief}» сохраняет предложения автора слово в слово.',
      anchor_t: 'ИИ указывает на источник', anchor_d: 'Каждая строка, написанная ИИ, отсылает к предложениям, на которые она опирается. Цвет показывает роль каждого абзаца в рассуждении.',
      original_t: 'Рядом с оригиналом', original_d: 'Для статьи или PDF кнопка «{original}» вверху открывает страницы слева. Каждый абзац отмечен там, где он стоит на странице.',
      ask_t: 'Спросите и увидите основания', ask_d: 'Выделите фрагмент и задайте вопрос о нём или спросите обо всём тексте. Каждый ответ ссылается на абзацы, на которые опирается, в один щелчок.',
      notes_t: 'Заметки сохраняются при масштабе', notes_d: 'Выделяйте и пишите на полях. Отмеченное остаётся, когда вы уменьшаете масштаб. «Сохранить» сохраняет страницу с заметками в один файл или только заметки.',
      done: 'Понятно', prev: 'Предыдущая страница', next: 'Следующая страница', close: 'Закрыть',
      k_levels: 'уровни', k_scroll: 'прокрутка', k_zoom: 'масштаб', k_lang: 'следующий язык', k_pages: 'страницы', k_close: 'закрыть',
    },
    hi: {
      title: 'TextFold कैसे काम करता है', sub: 'चार छोटे पन्ने: मुड़ने वाले पाठ को कैसे पढ़ें।',
      p1: 'पाठ लाएँ', p2: 'मोड़ें और खोलें', p3: 'किसके शब्द', p4: 'गहराई से पढ़ें',
      link_t: 'लिंक चिपकाएँ', link_d: 'कोई भी वेब पेज चलेगा। शोधपत्र का लिंक या DOI, विकिपीडिया और GitHub अपने खंडों, सूत्रों और चित्रों के साथ पढ़े जाते हैं।',
      file_t: 'या फ़ाइल छोड़ें', file_d: 'PDF, Word, PowerPoint या Excel फ़ाइल, EPUB, चित्र, Markdown या .txt। अगर PDF किसी ऑनलाइन शोधपत्र से मेल खाता है, तो वही संस्करण खुलता है। बाकी PDF और फ़ाइलें आपके कंप्यूटर पर बदली जाती हैं।',
      lang_t: 'अपनी भाषा में पढ़ें', lang_d: 'कई भाषाओं में से चुनें। पाठ का अनुवाद वाक्य दर वाक्य होता है, और हर सूत्र जैसा था वैसा रहता है।',
      levels_t: 'पाँच स्तर', levels_d: '{levels}। हर स्तर पर हर अनुच्छेद का कम हिस्सा बचता है, इसलिए स्क्रीन पर पूरे पाठ का ज़्यादा हिस्सा दिखता है।',
      map_t: 'पाठ का नक्शा', map_d: '{topic} स्तर पर पाठ खंडों के कार्डों का नक्शा बन जाता है। कोई खंड पढ़ने से पहले ही दिखता है कि खंड एक दूसरे पर कैसे टिके हैं।',
      zoom_t: 'उँगलियों से ज़ूम', zoom_d: 'पिंच करें, ⌘ या Ctrl के साथ स्क्रॉल करें, 1 से 5 दबाएँ, या स्तर पट्टी खींचें। जो शब्द बचते हैं, वे अपनी नई जगह पर खिसक जाते हैं। किसी अनुच्छेद पर क्लिक करें, तो सिर्फ़ वही खुलता है।',
      voice_t: 'सेरिफ़ यानी लेखक', voice_d: 'लेखक के शब्द सेरिफ़ फ़ॉन्ट में हैं, AI के शब्द बिना सेरिफ़ वाले फ़ॉन्ट में। {brief} स्तर लेखक के वाक्यों को शब्दशः रखता है।',
      anchor_t: 'AI अपना स्रोत दिखाता है', anchor_d: 'AI की लिखी हर पंक्ति उन वाक्यों की ओर इशारा करती है जिन पर वह टिकी है। रंग बताते हैं कि तर्क में हर अनुच्छेद की भूमिका क्या है।',
      original_t: 'मूल के साथ', original_d: 'शोधपत्र या PDF के लिए, ऊपर “{original}” बाईं ओर उसके पन्ने खोलता है। हर अनुच्छेद पन्ने पर अपनी जगह पर चिह्नित दिखता है।',
      ask_t: 'पूछें, और प्रमाण देखें', ask_d: 'किसी अंश को चुनकर उसके बारे में पूछें, या पूरे पाठ के बारे में पूछें। हर उत्तर उन अनुच्छेदों का हवाला देता है जिन पर वह टिका है, बस एक क्लिक दूर।',
      notes_t: 'नोट्स जो ज़ूम के साथ रहते हैं', notes_d: 'हाइलाइट करें और हाशिये में लिखें। ज़ूम आउट करने पर भी आपके चिह्नित अंश बने रहते हैं। ‘सहेजें’ से पेज को नोटों समेत एक फ़ाइल में, या सिर्फ़ नोट सहेजें।',
      done: 'समझ गया', prev: 'पिछला पन्ना', next: 'अगला पन्ना', close: 'बंद करें',
      k_levels: 'स्तर', k_scroll: 'स्क्रॉल', k_zoom: 'ज़ूम', k_lang: 'अगली भाषा', k_pages: 'पन्ने', k_close: 'बंद करें',
    },
  };

  // ── the pictures: TextFold's own marks. The author's text in grey lines (and a serif), AI's in the accent (and a sans).
  const L = (x, y, w, c = 'tk-ink') => `<rect class="${c}" x="${x}" y="${y}" width="${w}" height="2.4" rx="1.2"/>`;
  const chip = (x, y, w, t) => `<rect class="tk-card" x="${x}" y="${y}" width="${w}" height="10" rx="5"/><text class="tk-mono" x="${x + w / 2}" y="${y + 7}" text-anchor="middle">${t}</text>`;
  const head = (x1, y1, x2, y2) => {   // a small arrowhead at (x2, y2), pointing from (x1, y1)
    const a = Math.atan2(y2 - y1, x2 - x1), p = (s) => `${(x2 - 4 * Math.cos(a) + s * 2.4 * Math.sin(a)).toFixed(1)} ${(y2 - 4 * Math.sin(a) - s * 2.4 * Math.cos(a)).toFixed(1)}`;
    return `<path class="tk-acc" d="M${x2} ${y2}L${p(1)}L${p(-1)}Z"/>`;
  };
  const file = (x, rot, label, cls) => `<g transform="rotate(${rot} ${x + 9} 18)"><path class="tk-card" d="M${x} 5h13l5 5v20h-18z"/><path class="tk-s-line" d="M${x + 13} 5v5h5"/>`
    + `${L(x + 3, 13, 10, 'tk-line')}${L(x + 3, 17, 12, 'tk-line')}<text class="tk-mono tk-b ${cls}" x="${x + 9}" y="27" text-anchor="middle">${label}</text></g>`;
  const ART = {
    link: chip(11, 8, 24, 'arXiv') + chip(39, 8, 17, 'DOI') + chip(60, 8, 19, 'wiki') + chip(83, 8, 26, 'GitHub')
      + '<rect class="tk-card" x="8" y="29" width="104" height="22" rx="8"/>'
      + '<path class="tk-s-muted" d="M17.5 42.5l4-4M16 40l-1 1a2.4 2.4 0 0 0 3.4 3.4l1-1M23 41l1-1a2.4 2.4 0 0 0-3.4-3.4l-1 1"/>'
      + '<text class="tk-mono" x="29" y="42.3" style="font-size:6.5px">https://…</text>'
      + '<rect class="tk-acc" x="92" y="33" width="16" height="14" rx="5"/><path d="M96.5 40h7M101 37l3 3-3 3" fill="none" stroke="#fff" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>'
      + L(12, 60, 54, 'tk-line') + L(70, 60, 22, 'tk-line'),
    file: file(26, -7, 'PDF', 'tk-red') + file(51, 0, 'MD', 'tk-accent') + file(76, 7, 'TXT', '')
      + '<rect class="tk-soft" x="10" y="38" width="100" height="28" rx="7"/><rect class="tk-s-acc tk-dash" x="10" y="38" width="100" height="28" rx="7"/>'
      + '<path class="tk-s-acc" d="M60 59.5v-13M55.5 50.5L60 46l4.5 4.5"/>',
    lang: '<rect class="tk-card" x="8" y="7" width="44" height="58" rx="5"/><rect class="tk-card" x="68" y="7" width="44" height="58" rx="5"/>'
      + '<text class="tk-mono" x="13" y="16">EN</text><text class="tk-mono" x="73" y="16" style="font-family:var(--sans)">中</text>'
      + '<rect class="tk-soft" x="11" y="34" width="38" height="11" rx="2"/><rect class="tk-soft" x="71" y="34" width="38" height="11" rx="2"/>'
      + L(13, 23, 34) + L(13, 27.5, 24) + L(13, 37, 32, 'tk-ink tk-strong') + L(13, 41.5, 20, 'tk-ink tk-strong') + L(13, 51, 34) + L(13, 55.5, 28)
      + L(73, 23, 30) + L(73, 27.5, 18) + L(73, 37, 34, 'tk-ink tk-strong') + L(73, 41.5, 14, 'tk-ink tk-strong') + L(73, 51, 26) + L(73, 55.5, 32)
      + '<path class="tk-s-line" d="M52 26 C60 26 60 26 68 26M52 53 C60 53 60 53 68 53"/><path class="tk-s-acc" d="M52 39.5 C60 39.5 60 39.5 68 39.5"/>',
    levels: [[6, [14, 13, 14, 12, 14, 9], 'tk-ink'], [3, [14, 12, 10], 'tk-ink'], [2, [14, 11], 'tk-acc tk-ai'], [1, [13], 'tk-acc tk-ai']]
      .map(([n, ws, c], i) => ws.slice(0, n).map((w, j) => L(9 + i * 23, 8 + j * 5, w, c)).join('')).join('')
      + '<rect class="tk-s-acc" x="101" y="7" width="14" height="7" rx="3.5"/>'
      + '<rect class="tk-line" x="11" y="50.5" width="99" height="3" rx="1.5"/><rect class="tk-acc tk-ai" x="11" y="50.5" width="51" height="3" rx="1.5"/>'
      + [16, 39, 62, 85, 108].map((x, i) => `<circle class="${i === 2 ? 'tk-acc' : 'tk-card'}" cx="${x}" cy="52" r="${i === 2 ? 4 : 3}"/><text class="tk-mono${i === 2 ? ' tk-accent tk-b' : ''}" x="${x}" y="66" text-anchor="middle">${i + 1}</text>`).join(''),
    map: [[8, 8, 'gap'], [68, 8, 'method'], [8, 44, 'result'], [68, 44, 'claim']]
      .map(([x, y, r], i) => `<rect class="tk-card" x="${x}" y="${y}" width="44" height="20" rx="4"/><rect style="fill:var(--r-${r})" x="${x}" y="${y + 3}" width="2.5" height="14" rx="1.25"/>`
        + L(x + 7, y + 6, [26, 30, 22, 28][i], 'tk-acc tk-ai') + L(x + 7, y + 11.5, [18, 14, 20, 16][i], 'tk-line')).join('')
      + '<path class="tk-s-acc" d="M52 18H64M30 28V40M90 28V40M52 54H64"/>' + head(52, 18, 67, 18) + head(30, 28, 30, 43) + head(90, 28, 90, 43) + head(52, 54, 67, 54),
    zoom: (() => {
      const rows = [[[8, 10], [20, 14, 1], [36, 8], [46, 12], [60, 10]], [[8, 12], [22, 9], [33, 16, 1], [51, 10], [63, 7]], [[8, 14], [24, 10], [36, 12], [50, 9, 1], [61, 9]], [[8, 10], [20, 16]]];
      let s = '';
      rows.forEach((r, i) => r.forEach(([x, w, keep]) => { s += `<rect class="${keep ? 'tk-acc' : 'tk-line'}" x="${x}" y="${7 + i * 6}" width="${w}" height="3" rx="1.5"/>`; }));
      s += '<rect class="tk-acc" x="8" y="45" width="14" height="3" rx="1.5"/><rect class="tk-acc" x="25" y="45" width="16" height="3" rx="1.5"/><rect class="tk-acc" x="44" y="45" width="9" height="3" rx="1.5"/>';
      s += '<path class="tk-s-acc tk-dash tk-thin" d="M27 10.5 C27 30 15 32 15 43M41 16.5 C41 32 33 34 33 43M54.5 22.5 C54.5 34 48.5 36 48.5 43"/>';
      s += '<rect class="tk-card" x="79" y="8" width="14" height="14" rx="3.5"/><text x="86" y="18.6" text-anchor="middle" class="tk-key">⌘</text>'
        + '<text class="tk-mono" x="97" y="17.5" text-anchor="middle">+</text><rect class="tk-card" x="101" y="6" width="12" height="18" rx="6"/><path class="tk-s-muted" d="M107 9.5v4"/>';
      s += [1, 2, 3, 4, 5].map((n, i) => `<rect class="${n === 3 ? 'tk-acc' : 'tk-card'}" x="${8 + i * 14}" y="55" width="11" height="11" rx="3"/><text class="tk-mono${n === 3 ? ' tk-white' : ''}" x="${13.5 + i * 14}" y="62.6" text-anchor="middle">${n}</text>`).join('');
      s += '<path class="tk-s-muted" d="M91 56l4 4.5-4 4.5M109 56l-4 4.5 4 4.5"/>';   // a pinch
      return s;
    })(),
    voice: '<rect class="tk-line" x="59.5" y="10" width="1" height="54"/>'
      + '<text class="tk-serif" x="34" y="31" text-anchor="middle" style="font-size:23px">Aa</text>'
      + '<text class="tk-mono" x="13" y="14" style="font-family:var(--serif);font-size:12px">“</text><text class="tk-mono" x="50" y="14" style="font-family:var(--serif);font-size:12px">”</text>'
      + L(12, 42, 40) + L(12, 47.5, 36) + L(12, 53, 28)
      + '<text class="tk-sans" x="86" y="31" text-anchor="middle" style="font-size:23px;font-weight:550">Aa</text>'
      + '<path class="tk-acc" d="M106 9.5l1.3 3.4 3.4 1.3-3.4 1.3-1.3 3.4-1.3-3.4-3.4-1.3 3.4-1.3z"/>'
      + L(68, 42, 40, 'tk-acc tk-ai') + L(68, 47.5, 26, 'tk-acc tk-ai'),
    anchor: '<rect class="tk-soft" x="8" y="6" width="104" height="14" rx="4"/>' + L(14, 11.8, 70, 'tk-acc') + '<circle class="tk-acc" cx="104" cy="13" r="2.2"/>'
      + '<rect class="tk-card" x="8" y="28" width="104" height="38" rx="4"/><rect style="fill:var(--r-claim)" x="8" y="31" width="2.5" height="32" rx="1.25"/>'
      + '<rect class="tk-soft" x="14" y="38.3" width="84" height="6" rx="1.5"/><rect class="tk-soft" x="14" y="50.3" width="66" height="6" rx="1.5"/>'
      + L(16, 34, 88) + L(16, 40, 80, 'tk-ink tk-strong') + L(16, 46, 90) + L(16, 52, 62, 'tk-ink tk-strong') + L(16, 58, 54)
      + '<path class="tk-s-acc tk-dash tk-thin" d="M34 15.5 C34 28 26 30 24 38.3M70 15.5 C70 34 60 40 58 50.3"/>',
    original: '<rect class="tk-card" x="8" y="6" width="46" height="60" rx="3"/>'
      + [12, 17, 22, 28, 33, 38, 44, 49, 54].map((y, i) => L(13, y, [36, 34, 30, 36, 33, 26, 36, 35, 20][i], 'tk-line')).join('')
      + '<rect class="tk-soft" x="11" y="26" width="40" height="15" rx="2"/><rect class="tk-s-acc" x="11" y="26" width="40" height="15" rx="2"/>'
      + L(13, 28, 36, 'tk-ink') + L(13, 33, 33, 'tk-ink') + L(13, 38, 26, 'tk-ink')
      + '<text class="tk-mono" x="13" y="62.5" style="font-size:5px">PDF · 2/9</text>'
      + '<rect class="tk-card" x="64" y="6" width="48" height="60" rx="3"/><rect class="tk-soft" x="66" y="29" width="44" height="17" rx="2"/>'
      + L(69, 13, 36) + L(69, 18, 30) + L(69, 32, 38, 'tk-ink tk-strong') + L(69, 37, 34, 'tk-ink tk-strong') + L(69, 42, 22, 'tk-ink tk-strong') + L(69, 52, 36) + L(69, 57, 28)
      + '<path class="tk-s-acc" d="M51 33.5 C58 33.5 59 37.5 66 37.5"/>',
    ask: '<rect class="tk-card" x="8" y="6" width="56" height="60" rx="4"/><rect class="tk-hl" x="11" y="21.8" width="50" height="10.4" rx="1.5"/>'
      + [12, 18, 24, 29.5, 36, 42, 48, 54, 60].map((y, i) => L(14, y, [44, 40, 44, 30, 42, 38, 44, 36, 24][i], i === 2 || i === 3 ? 'tk-ink tk-strong' : 'tk-ink')).join('')
      + '<rect class="tk-card" x="72" y="6" width="40" height="15" rx="7.5"/><text class="tk-sans" x="92" y="16.8" text-anchor="middle" style="font-size:9px;font-weight:650">?</text>'
      + '<rect class="tk-soft" x="72" y="27" width="40" height="33" rx="5"/>' + L(77, 33, 30, 'tk-acc tk-ai') + L(77, 38, 24, 'tk-acc tk-ai')
      + '<rect class="tk-card" x="77" y="45" width="14" height="9" rx="4.5"/><text class="tk-mono tk-accent" x="84" y="51.6" text-anchor="middle" style="font-size:5.5px">¶3</text>'
      + '<rect class="tk-card" x="94" y="45" width="14" height="9" rx="4.5"/><text class="tk-mono tk-accent" x="101" y="51.6" text-anchor="middle" style="font-size:5.5px">¶7</text>'
      + '<path class="tk-s-acc tk-dash tk-thin" d="M77 49.5 C68 49.5 70 27 61 27"/>',
    notes: '<rect class="tk-card" x="6" y="7" width="46" height="52" rx="4"/><rect class="tk-hl" x="8.5" y="17.6" width="40" height="11" rx="1.5"/>'
      + [13, 19, 25, 31, 37, 43, 49].map((y, i) => L(10, y, [38, 36, 30, 38, 34, 38, 22][i], i === 1 || i === 2 ? 'tk-ink tk-strong' : 'tk-ink')).join('')
      + '<rect class="tk-note" x="40" y="30" width="22" height="16" rx="2"/>' + L(43, 35, 15, 'tk-warm') + L(43, 40, 10, 'tk-warm')
      + '<path class="tk-s-muted" d="M66 31h9M72 28l3 3-3 3"/>'
      + '<rect class="tk-card" x="79" y="12" width="35" height="30" rx="4"/>' + L(83, 19, 26, 'tk-acc tk-ai') + '<rect class="tk-hl" x="81" y="25.8" width="31" height="6.4" rx="1.5"/>' + L(83, 27.8, 24, 'tk-ink tk-strong') + L(83, 35, 20, 'tk-acc tk-ai')
      + chip(32, 56, 16, 'MD') + chip(51, 56, 18, 'PDF') + chip(72, 56, 22, 'JSON'),
  };

  const CSS = `
.tt-back { position: fixed; inset: 0; z-index: 2000; display: flex; align-items: center; justify-content: center; padding: 24px;
  background: rgba(26, 20, 10, .34); font-family: var(--sans); animation: tt-in .18s var(--ease, ease-out); }
.tt { position: relative; display: flex; flex-direction: column; width: min(960px, 100%); max-height: calc(100vh - 48px); max-height: calc(100dvh - 48px);
  background: var(--card); color: var(--ink); border: 1px solid var(--line); border-radius: 18px; box-shadow: 0 30px 80px -30px rgba(30, 20, 5, .5); outline: none; }
.tt-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; padding: 20px 18px 2px 28px; flex: none; }
.tt-head > div { display: flex; align-items: baseline; flex-wrap: wrap; gap: 2px 12px; min-width: 0; }
.tt-title { margin: 0; font: 650 18px/1.3 var(--sans); letter-spacing: -.01em; color: var(--ink); }
.tt-sub { margin: 0; font-size: 13px; line-height: 1.4; color: var(--muted); }
.tt-x { flex: none; width: 32px; height: 32px; display: grid; place-items: center; border: 0; border-radius: 8px; background: none; color: var(--muted); cursor: pointer; }
.tt-x:hover { background: var(--surface); color: var(--ink); }
.tt-x svg { width: 16px; height: 16px; }
.tt-body { padding: 12px 28px 4px; overflow-y: auto; overscroll-behavior: contain; min-height: 0; }
/* every page in one place, the others unseen there: the dialog the tallest page's height, so the arrows, dots and keys
   stay put on any screen (#141; a fixed least height did not hold where a page is taller, and a phone's moved most) */
.tt-pages { display: grid; }
.tt-page { grid-area: 1 / 1; min-width: 0; }
.tt-page.off { visibility: hidden; }
.tt-label { margin: 0 0 12px; font: 650 11.5px/1.4 var(--sans); letter-spacing: .06em; text-transform: uppercase; color: var(--accent); }
.tt-grid { display: grid; grid-template-columns: repeat(var(--n), minmax(0, 1fr)); gap: 14px; }
.tt-card { display: flex; flex-direction: column; min-width: 0; padding: 14px; border-radius: 14px; background: var(--surface); border: 1px solid var(--line-2);
  animation: tt-in .22s var(--ease, ease-out) both; }
.tt-card:nth-child(2) { animation-delay: .04s; } .tt-card:nth-child(3) { animation-delay: .08s; }
.tt-art { flex: none; width: 100%; aspect-ratio: 120 / 72; max-height: 150px; margin-bottom: 12px; border-radius: 10px; background: var(--card); border: 1px solid var(--line-2); overflow: hidden; }
.tt-art svg { display: block; width: 100%; height: 100%; }
.tt-card h3 { margin: 0; font: 650 14px/1.35 var(--sans); color: var(--ink); }
.tt-card p { margin: 6px 0 0; font-size: 13px; line-height: 1.55; color: var(--ink-2); overflow-wrap: anywhere; }
.tt-nav { flex: none; display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 14px 28px; }
.tt-arrow { width: 34px; height: 34px; display: grid; place-items: center; border: 1px solid var(--line); border-radius: 9px; background: var(--card); color: var(--muted); cursor: pointer; }
.tt-arrow:hover:not(:disabled) { color: var(--ink); border-color: var(--rule); }
.tt-arrow:disabled { opacity: .3; cursor: default; }
.tt-arrow svg { width: 16px; height: 16px; }
.tt-dots { display: flex; align-items: center; gap: 4px; }
.tt-dots button { width: 22px; height: 22px; padding: 0; border: 0; background: none; cursor: pointer; display: grid; place-items: center; }
.tt-dots i { display: block; width: 8px; height: 8px; border-radius: 99px; background: var(--line); transition: width .2s var(--ease, ease-out), background-color .2s; }
.tt-dots button:hover i { background: var(--faint); }
.tt-dots button[aria-current="step"] { width: 34px; }
.tt-dots button[aria-current="step"] i { width: 24px; background: var(--accent); }
.tt-done { height: 34px; padding: 0 18px; border: 0; border-radius: 9px; background: var(--accent); color: #fff; font: 600 13.5px var(--sans); cursor: pointer; }
.tt-done:hover { background: var(--accent-deep); }
:root:not([data-theme="light"]) .tt-done { color: #fff; }
.tt button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.tt-keys { flex: none; display: flex; flex-wrap: wrap; gap: 4px 16px; padding: 10px 28px 14px; border-top: 1px solid var(--line-2); font-size: 12px; line-height: 20px; color: var(--muted); }
.tt-keys kbd { display: inline-block; min-width: 18px; padding: 0 5px; margin-right: 2px; text-align: center; font: 500 11px/17px var(--mono); color: var(--ink-2);
  background: var(--card); border: 1px solid var(--line); border-bottom-width: 2px; border-radius: 5px; }
.tt-art .tk-card { fill: var(--card); stroke: var(--line); stroke-width: 1; }
.tt-art .tk-line { fill: var(--line); }
.tt-art .tk-ink { fill: var(--ink-2); opacity: .38; }
.tt-art .tk-ink.tk-strong { opacity: .62; }
.tt-art .tk-acc { fill: var(--accent); }
.tt-art .tk-acc.tk-ai { opacity: .6; }
.tt-art .tk-soft { fill: var(--accent-soft); }
.tt-art .tk-hl { fill: var(--hl); opacity: .7; }
.tt-art .tk-warm { fill: var(--warm); opacity: .7; }
.tt-art .tk-note { fill: color-mix(in srgb, var(--warm) 16%, var(--card)); stroke: var(--warm); stroke-width: .8; }
.tt-art .tk-s-acc, .tt-art .tk-s-line, .tt-art .tk-s-muted { fill: none; stroke-width: 1.2; stroke-linecap: round; stroke-linejoin: round; }
.tt-art .tk-s-acc { stroke: var(--accent); }
.tt-art .tk-s-line { stroke: var(--line); }
.tt-art .tk-s-muted { stroke: var(--muted); }
.tt-art .tk-dash { stroke-dasharray: 3 2.5; }
.tt-art .tk-thin { stroke-width: .9; }
.tt-art text { font-family: var(--sans); }
.tt-art .tk-mono { font-family: var(--mono); font-size: 6px; fill: var(--muted); }
.tt-art .tk-b { font-weight: 650; }
.tt-art .tk-red { fill: var(--r-limitation); }
.tt-art .tk-accent { fill: var(--accent); }
.tt-art .tk-white { fill: #fff; }
.tt-art .tk-key { font-size: 8.5px; fill: var(--ink-2); }
.tt-art .tk-serif { font-family: var(--serif); fill: var(--ink); }
.tt-art .tk-sans { font-family: var(--sans); fill: var(--accent); }
@keyframes tt-in { from { opacity: 0; transform: translateY(4px); } }
@media (prefers-reduced-motion: reduce) { .tt-back, .tt-card { animation: none; } .tt-dots i { transition: none; } }
@media (max-width: 720px) {
  .tt-back { padding: 10px; }
  .tt { max-height: calc(100vh - 20px); max-height: calc(100dvh - 20px); border-radius: 16px; }
  .tt-head { padding: 16px 10px 0 18px; }
  .tt-body { padding: 10px 18px 2px; }
  .tt-grid { grid-template-columns: minmax(0, 1fr); gap: 10px; }
  .tt-card { flex-direction: row; align-items: flex-start; gap: 12px; padding: 10px; }
  .tt-art { width: 38%; max-height: none; margin: 0; }
  .tt-card > div { min-width: 0; flex: 1; }
  .tt-nav { padding: 12px 18px; }
  .tt-keys { display: none; }
}`;

  const ICON = {
    x: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8"/></svg>',
    prev: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 3.5L5.5 8l4.5 4.5"/></svg>',
    next: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 3.5L10.5 8 6 12.5"/></svg>',
  };

  let root = null, dialog = null, page = 0, lang = 'en', before = null, overflow = '';

  // the language asked for; else the page's own (<html lang>), as I18N names it
  function pick(l) {
    const codes = Object.keys(W);
    if (l && W[l]) return l;
    const h = (document.documentElement.lang || '').toLowerCase();
    if (h.startsWith('zh')) return /tw|hk|mo|hant/.test(h) ? 'zh-Hant' : 'zh';
    return codes.includes(h.split('-')[0]) ? h.split('-')[0] : 'en';
  }
  function words() {
    const w = { ...W.en, ...W[lang] };
    const T = window.DR_I18N ? window.DR_I18N.t(lang) : {};
    const lv = T.levels || ['Topic', 'Takeaway', 'Key', 'Brief', 'Full'];
    const vars = { levels: lv.slice().reverse().join(' · '), topic: lv[0], brief: lv[3], original: T.compare || 'Original' };
    for (const k of Object.keys(w)) w[k] = w[k].replace(/\{(\w+)\}/g, (m, v) => esc(vars[v] ?? m));
    return w;
  }

  function render() {
    const w = words(), last = page === PAGES.length - 1;
    const keepFocus = dialog.contains(document.activeElement) && document.activeElement.closest('.tt-nav');
    // every page, the one shown and the others laid out in the same place unseen (.off): the dialog is as tall as the
    // tallest page in this language at this width, and nothing below moves from one page to the next (#141)
    dialog.querySelector('.tt-pages').innerHTML = PAGES.map((ideas, i) => `<div class="tt-page${i === page ? '' : ' off'}">`
      + `<p class="tt-label">${esc(w['p' + (i + 1)])}</p><div class="tt-grid" style="--n:${ideas.length}">`
      + ideas.map((k) => `<div class="tt-card"><div class="tt-art"><svg viewBox="0 0 120 72" aria-hidden="true">${ART[k]}</svg></div>`
        + `<div><h3>${w[k + '_t']}</h3><p>${w[k + '_d']}</p></div></div>`).join('') + '</div></div>').join('');
    dialog.querySelector('.tt-body').scrollTop = 0;
    const nav = dialog.querySelector('.tt-nav');
    nav.innerHTML = `<button type="button" class="tt-arrow" data-go="-1" aria-label="${esc(w.prev)}" title="${esc(w.prev)}"${page === 0 ? ' disabled' : ''}>${ICON.prev}</button>`
      + `<div class="tt-dots">${PAGES.map((_, i) => `<button type="button" data-page="${i}" aria-label="${i + 1} / ${PAGES.length}"${i === page ? ' aria-current="step"' : ''}><i></i></button>`).join('')}</div>`
      + (last ? `<button type="button" class="tt-done">${esc(w.done)}</button>`
        : `<button type="button" class="tt-arrow" data-go="1" aria-label="${esc(w.next)}" title="${esc(w.next)}">${ICON.next}</button>`);
    if (keepFocus) (nav.querySelector('.tt-done') || nav.querySelector('[data-go="1"]')).focus();
  }

  function go(n) {
    const p = Math.max(0, Math.min(PAGES.length - 1, n));
    if (p === page) return;
    page = p; render();
  }

  // while it is open the page behind takes no keys, no wheel and no pinch: the reader's 1–5, L, ⌘-scroll and Esc are its own
  function onKey(e) {
    if (!root) return;
    e.stopPropagation();
    if (e.key === 'Escape') { e.preventDefault(); close(); }
    else if (e.key === 'ArrowRight' && !e.metaKey && !e.ctrlKey) { e.preventDefault(); go(page + 1); }
    else if (e.key === 'ArrowLeft' && !e.metaKey && !e.ctrlKey) { e.preventDefault(); go(page - 1); }
    else if (e.key === 'Tab') {   // the focus stays inside
      const f = [...dialog.querySelectorAll('button:not(:disabled)')];
      if (!f.length) return;
      const i = f.indexOf(document.activeElement);
      if (e.shiftKey && i <= 0) { e.preventDefault(); f[f.length - 1].focus(); }
      else if (!e.shiftKey && i === f.length - 1) { e.preventDefault(); f[0].focus(); }
      else if (i < 0) { e.preventDefault(); f[0].focus(); }
    }
  }
  function onWheel(e) {
    if (!root) return;
    e.stopPropagation();
    if (e.ctrlKey || e.metaKey) e.preventDefault();
  }
  function onGesture(e) { if (root) { e.stopPropagation(); e.preventDefault(); } }

  function open(l) {
    lang = pick(l); page = 0;
    if (root) { render(); return; }
    if (!document.getElementById('tt-style')) document.head.append(Object.assign(document.createElement('style'), { id: 'tt-style', textContent: CSS }));
    before = document.activeElement;
    const w = words();
    root = document.createElement('div');
    root.className = 'tt-back';
    root.innerHTML = `<div class="tt" role="dialog" aria-modal="true" aria-labelledby="tt-title" tabindex="-1" lang="${esc(window.DR_I18N ? window.DR_I18N.htmlLang(lang) : lang)}">`
      + `<div class="tt-head"><div><h2 class="tt-title" id="tt-title">${esc(w.title)}</h2><p class="tt-sub">${esc(w.sub)}</p></div>`
      + `<button type="button" class="tt-x" aria-label="${esc(w.close)}" title="${esc(w.close)}">${ICON.x}</button></div>`
      + '<div class="tt-body"><div class="tt-pages"></div></div><div class="tt-nav"></div>'
      + `<div class="tt-keys"><span><kbd>1</kbd>–<kbd>5</kbd> ${esc(w.k_levels)}</span><span><kbd>⌘</kbd>/<kbd>Ctrl</kbd> + ${esc(w.k_scroll)} ${esc(w.k_zoom)}</span>`
      + `<span><kbd>L</kbd> ${esc(w.k_lang)}</span><span><kbd>←</kbd><kbd>→</kbd> ${esc(w.k_pages)}</span><span><kbd>Esc</kbd> ${esc(w.k_close)}</span></div></div>`;
    dialog = root.firstChild;
    root.addEventListener('click', (e) => {
      if (e.target === root) { close(); return; }
      const b = e.target.closest('button');
      if (!b) return;
      if (b.classList.contains('tt-x') || b.classList.contains('tt-done')) close();
      else if (b.dataset.go) go(page + +b.dataset.go);
      else if (b.dataset.page) go(+b.dataset.page);
    });
    for (const ev of ['pointerdown', 'touchstart', 'touchmove', 'mousedown', 'dblclick']) root.addEventListener(ev, (e) => e.stopPropagation());
    addEventListener('keydown', onKey, true);
    addEventListener('wheel', onWheel, { capture: true, passive: false });
    for (const ev of ['gesturestart', 'gesturechange', 'gestureend']) addEventListener(ev, onGesture, true);
    document.body.append(root);
    overflow = document.documentElement.style.overflow;
    document.documentElement.style.overflow = 'hidden';
    render();
    dialog.focus();   // the keys work at once; Tab reaches the buttons
  }

  function close() {
    if (!root) return;
    store.set(KEY, '1');
    removeEventListener('keydown', onKey, true);
    removeEventListener('wheel', onWheel, { capture: true });
    for (const ev of ['gesturestart', 'gesturechange', 'gestureend']) removeEventListener(ev, onGesture, true);
    root.remove(); root = null; dialog = null;
    document.documentElement.style.overflow = overflow;
    if (before && before.focus && before.isConnected) before.focus({ preventScroll: true });
    before = null;
  }

  window.TFTutorial = { open, close, seen: () => !!store.get(KEY), isOpen: () => !!root };
})();
