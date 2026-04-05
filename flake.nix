{
  description = "ZariBox - declarative container manager";

  inputs.nixpkgs.url = "github:nixos/nixpkgs/nixos-25.11";

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
    in {
      packages.${system}.default = pkgs.python3Packages.buildPythonApplication {
        pname = "zaribox";
        version = "0.1.5";
        src = ./.;
        format = "pyproject";
        propagatedBuildInputs = with pkgs.python3Packages; [ pyyaml setuptools ];
      };

      devShells.${system}.default = pkgs.mkShell {
        buildInputs = [
          (pkgs.python3.withPackages (ps: [ ps.pyyaml ps.pytest ]))
          pkgs.git
        ];
        shellHook = ''
          echo "ZariBox dev shell"
          echo "Python $(python --version)"
        '';
      };
    };
}