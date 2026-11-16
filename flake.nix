{
  description = "TextFold: zoom any text from every word to a one-screen map";

  # 26.05 also supports Intel Macs, which newer Nixpkgs releases no longer do.
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
    in
    {
      packages = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          textfold = pkgs.callPackage ./nix/package.nix { };
        in
        {
          inherit textfold;
          default = textfold;
        }
      );

      apps = forAllSystems (
        system:
        let
          textfold = {
            type = "app";
            program = "${self.packages.${system}.textfold}/bin/textfold";
            meta.description = self.packages.${system}.textfold.meta.description;
          };
        in
        {
          inherit textfold;
          default = textfold;
        }
      );

      checks = forAllSystems (system: {
        inherit (self.packages.${system}) textfold;
      });
    };
}
