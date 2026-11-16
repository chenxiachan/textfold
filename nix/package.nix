{ lib, python312Packages }:

let
  pyproject = builtins.fromTOML (builtins.readFile ../pyproject.toml);
  pdf-inspector = python312Packages.callPackage ./pdf-inspector.nix { };
  dependencies = with python312Packages; [
    beautifulsoup4
    lxml
    pillow
    markdown-it-py
    mdit-py-plugins
    latex2mathml
    trafilatura
    lxml-html-clean
    mammoth
    openpyxl
    pdf-inspector
    pypdfium2
  ];
in
python312Packages.buildPythonApplication {
  pname = pyproject.project.name;
  version = builtins.head (
    builtins.match ''.*__version__ = "([^"]+)".*'' (builtins.readFile ../textfold/__init__.py)
  );
  pyproject = true;

  src = lib.fileset.toSource {
    root = ../.;
    fileset = lib.fileset.unions [
      ../pyproject.toml
      ../README.md
      ../LICENSE
      ../NOTICE
      (lib.fileset.fileFilter (file: !(file.hasExt "pyc" || file.hasExt "pyo")) ../textfold)
    ];
  };

  build-system = with python312Packages; [
    setuptools
    wheel
  ];

  inherit dependencies;

  # Browser native-messaging launchers reuse sys.executable and this Python path.
  makeWrapperArgs = [
    "--prefix"
    "PYTHONPATH"
    ":"
    "${builtins.placeholder "out"}/${python312Packages.python.sitePackages}:${python312Packages.makePythonPath dependencies}"
  ];

  pythonImportsCheck = [
    "textfold.server"
    "textfold.pdftext"
    "pdf_inspector"
    "pypdfium2"
  ];

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck
    "$out/bin/textfold" --help > /dev/null
    test -f "$out/${python312Packages.python.sitePackages}/textfold/web/reader.js"
    runHook postInstallCheck
  '';

  meta = {
    inherit (pyproject.project) description;
    homepage = pyproject.project.urls.Homepage;
    license = lib.licenses.asl20;
    mainProgram = "textfold";
    platforms = [
      "x86_64-linux"
      "aarch64-linux"
      "x86_64-darwin"
      "aarch64-darwin"
    ];
  };
}
