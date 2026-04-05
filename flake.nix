{
  description = "ZariBox nix shell";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-25.11";
  };

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
    in
    {
      devShells.${system}.default = pkgs.mkShell {
        buildInputs = [
          (pkgs.python3.withPackages (ps: [
            ps.pyyaml
            ps.pytest
          ]))
          pkgs.git
        ];

        shellHook = ''
          echo "ZariBox dev shell"
          echo "Python $(python --version)"
        '';
      };
    };
}