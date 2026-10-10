# Impure shell: uses whatever nixpkgs is on NIX_PATH.
# Enter with: nix-shell
{ pkgs ? import <nixpkgs> { } }:

pkgs.mkShell {
  packages = [
    (pkgs.python3.withPackages (ps: with ps; [
      numpy # imported directly in query_conditions.py
      pandas
      openpyxl
      pytest
      black
    ]))

  ];
}
