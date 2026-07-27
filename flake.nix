{
  description = "AI Guardrails solution documentation set — validation, generation and deck tooling";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAllSystems = f:
        nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      devShells = forAllSystems (pkgs:
        let
          # tools/deck builds the audience decks with python-pptx and
          # tools/xlsx builds the delivery workbook with openpyxl; the docs
          # validators need nothing beyond the standard library.
          python = pkgs.python3.withPackages (ps: [ ps.python-pptx ps.openpyxl ]);
        in
        {
          default = pkgs.mkShell {
            packages = [
              python
              pkgs.just
            ];
          };
        });
    };
}
