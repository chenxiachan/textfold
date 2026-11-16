{
  lib,
  stdenv,
  buildPythonPackage,
  fetchPypi,
  autoPatchelfHook,
}:

let
  # Upstream's ABI3 wheels support Python 3.8+ and avoid a separate Rust toolchain.
  wheels = {
    x86_64-linux = {
      platform = "manylinux_2_17_x86_64.manylinux2014_x86_64";
      hash = "sha256-CiWW/rxn0Rd9HcPlji02S+A8M/TjGAwTNn0ZGMsX0Bg=";
    };
    aarch64-linux = {
      platform = "manylinux_2_17_aarch64.manylinux2014_aarch64";
      hash = "sha256-Q25iAVSw2PKjZIrLUqHOFpMzpb0N1OiYjOFZ8X1ArcQ=";
    };
    x86_64-darwin = {
      platform = "macosx_10_12_x86_64";
      hash = "sha256-l2K7BKgtcmZyAf5Uqq3PL08Q/35/1Wy/oJ+/lp/u3Es=";
    };
    aarch64-darwin = {
      platform = "macosx_11_0_arm64";
      hash = "sha256-dmTYfUAMK0BXtuXTiXlUoh1KDuxogwYulw+kLXV2HaU=";
    };
  };
in
buildPythonPackage rec {
  pname = "pdf-inspector";
  version = "1.25.2";
  format = "wheel";

  src = fetchPypi {
    pname = "pdf_inspector";
    inherit version format;
    dist = "cp38";
    python = "cp38";
    abi = "abi3";
    inherit (wheels.${stdenv.hostPlatform.system}) platform hash;
  };

  nativeBuildInputs = lib.optionals stdenv.hostPlatform.isLinux [ autoPatchelfHook ];
  buildInputs = lib.optionals stdenv.hostPlatform.isLinux [ stdenv.cc.cc.lib ];

  pythonImportsCheck = [ "pdf_inspector" ];

  meta = {
    description = "PDF inspection, classification and text extraction";
    homepage = "https://github.com/firecrawl/pdf-inspector";
    license = lib.licenses.mit;
    platforms = builtins.attrNames wheels;
    sourceProvenance = [ lib.sourceTypes.binaryNativeCode ];
  };
}
