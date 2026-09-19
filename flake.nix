{
  description = "ZariBox - declarative container manager";

  inputs.nixpkgs.url = "github:nixos/nixpkgs/nixos-26.05";

  outputs =
    { self, nixpkgs }:
    let
      supportedSystems = [
        "x86_64-linux"
        "aarch64-linux"
      ];

      forAllSystems =
        f: nixpkgs.lib.genAttrs supportedSystems (system: f nixpkgs.legacyPackages.${system});

      version = "0.2.8.1";
    in
    {
      formatter = forAllSystems (pkgs: pkgs.nixfmt);

      packages = forAllSystems (pkgs: {
        default = pkgs.python3Packages.buildPythonApplication {
          pname = "zaribox";
          inherit version;

          src = pkgs.fetchFromGitHub {
            owner = "ZariTen";
            repo = "zaribox";
            rev = "v${version}";
            hash = "sha256-2IENH7soBf45JScLcM92k5lKAtMOb7eVVMwATvnvAAE=";
          };

          format = "pyproject";

          nativeBuildInputs = with pkgs.python3Packages; [
            setuptools
          ];

          propagatedBuildInputs = with pkgs.python3Packages; [
            pyyaml
          ];

          nativeCheckInputs = with pkgs.python3Packages; [
            pytestCheckHook
          ];

          meta = {
            description = "Declarative container manager";
            homepage = "https://github.com/ZariTen/zaribox";
            mainProgram = "zaribox";
          };
        };
      });

      checks = forAllSystems (pkgs: {
        zaribox = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
      });

      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          packages = [
            pkgs.git
            pkgs.nixfmt
            pkgs.python3
            pkgs.ruff
            pkgs.uv
          ];

          shellHook = ''
            echo "ZariBox dev shell"
            echo "Python $(python --version)"
          '';
        };
      });
    };
}
