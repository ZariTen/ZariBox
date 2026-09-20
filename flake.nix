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
        function:
        nixpkgs.lib.genAttrs supportedSystems (
          system: function nixpkgs.legacyPackages.${system}
        );

      version = "0.2.8.1";
    in
    {
      formatter = forAllSystems (pkgs: pkgs.nixfmt);

      packages = forAllSystems (
        pkgs:
        let
          zaribox = pkgs.python3Packages.buildPythonApplication {
            pname = "zaribox";
            inherit version;

            src = self;
            pyproject = true;

            build-system = [
              pkgs.python3Packages.setuptools
            ];

            dependencies = [
              pkgs.python3Packages.pyyaml
            ];

            nativeCheckInputs = [
              pkgs.python3Packages.pytestCheckHook
            ];

            pythonImportsCheck = [
              "zaribox"
            ];

            meta = {
              description = "Declarative Podman manager";
              homepage = "https://github.com/ZariTen/zaribox";
              license = pkgs.lib.licenses.gpl3Only;
              mainProgram = "zaribox";
              platforms = pkgs.lib.platforms.linux;
            };
          };
        in
        {
          inherit zaribox;
          default = zaribox;
        }
      );

      checks = forAllSystems (pkgs: {
        inherit (self.packages.${pkgs.stdenv.hostPlatform.system}) zaribox;
      });

      devShells = forAllSystems (
        pkgs:
        let
          python = pkgs.python3.withPackages (pythonPackages: [
            pythonPackages.pytest
            pythonPackages.pyyaml
          ]);
        in
        {
          default = pkgs.mkShell {
            packages = [
              pkgs.git
              pkgs.nixfmt
              python
              pkgs.ruff
              pkgs.uv
            ];

            shellHook = ''
              echo "ZariBox dev shell"
              echo "Python $(python --version)"
            '';
          };
        }
      );
    };
}
