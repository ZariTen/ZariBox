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

      cargoToml = builtins.fromTOML (builtins.readFile ./Cargo.toml);
    in
    {
      formatter = forAllSystems (pkgs: pkgs.nixfmt);

      packages = forAllSystems (
        pkgs:
        let
          zaribox = pkgs.rustPlatform.buildRustPackage {
            pname = "zaribox";
            inherit (cargoToml.package) version;

            src = pkgs.lib.fileset.toSource {
              root = ./.;
              fileset = pkgs.lib.fileset.unions [
                ./Cargo.toml
                ./Cargo.lock
                ./src
                ./examples # validated by the test suite
              ];
            };
            cargoLock.lockFile = ./Cargo.lock;

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

      apps = forAllSystems (
        pkgs:
        let
          zaribox = self.packages.${pkgs.stdenv.hostPlatform.system}.zaribox;
        in
        {
          default = {
            type = "app";
            program = "${zaribox}/bin/zaribox";
            meta.description = "Run the ZariBox CLI";
          };
          zaribox-mcp = {
            type = "app";
            program = "${zaribox}/bin/zaribox-mcp";
            meta.description = "Run the ZariBox MCP stdio server";
          };
        }
      );

      checks = forAllSystems (pkgs: {
        inherit (self.packages.${pkgs.stdenv.hostPlatform.system}) zaribox;
      });

      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          packages = [
            pkgs.cargo
            pkgs.rustc
            pkgs.clippy
            pkgs.rustfmt
            pkgs.git
            pkgs.nixfmt
          ];

          shellHook = ''
            echo "ZariBox dev shell"
            echo "$(rustc --version)"
          '';
        };
      });
    };
}
