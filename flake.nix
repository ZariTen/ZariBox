{
  description = "ZariBox - declarative container manager";

  inputs.nixpkgs.url = "github:nixos/nixpkgs/nixos-25.11";

  outputs = { self, nixpkgs }:
    let
      supportedSystems = [ "x86_64-linux" "aarch64-linux" ];
      
      forAllSystems = nixpkgs.lib.genAttrs supportedSystems;
    in {
      packages = forAllSystems (system:
        let
          pkgs = import nixpkgs { inherit system; };
        in {
          default = pkgs.python3Packages.buildPythonApplication {
            pname = "zaribox";
            version = "0.1.6.2";
            src = ./.;
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
          };
        });

      devShells = forAllSystems (system:
        let
          pkgs = import nixpkgs { inherit system; };
        in {
          default = pkgs.mkShell {
            inputsFrom = [ self.packages.${system}.default ];
            
            packages = [
              pkgs.python3Packages.flake8
              pkgs.git
            ];

            shellHook = ''
              echo "ZariBox dev shell"
              echo "Python $(python --version)"
            '';
          };
        });
    };
}