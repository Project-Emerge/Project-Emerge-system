{
  description = "Four-camera ArUco indoor localization system";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs {
          inherit system;
          config.allowUnfree = true;
        };

        pythonEnv = pkgs.python312;
        pythonPackages = pkgs.python312Packages;

        # List of native runtime libraries required by OpenCV and python wheels
        runtimeLibs = with pkgs; pkgs.lib.optionals pkgs.stdenv.hostPlatform.isLinux [
          stdenv.cc.cc.lib
          libGL
          glib
          libx11
          libxext
          libxrender
          libice
          libsm
          libxcb
          libz
          fontconfig
          freetype
          dbus
        ];

        # Packaged application
        vision-system = pythonPackages.buildPythonApplication {
          pname = "vision-system";
          version = "0.1.0";
          pyproject = true;

          src = ./.;

          nativeBuildInputs = [
            pythonPackages.hatchling
          ];

          propagatedBuildInputs = [
            pythonPackages.numpy
            pythonPackages.opencv4
            pythonPackages.paho-mqtt
            pythonPackages.pydantic
            pythonPackages.reportlab
            pythonPackages.scipy
          ];

          doCheck = false; # Tests might require camera hardware or display
        };

      in
      {
        packages.default = vision-system;

        devShells.default = pkgs.mkShell {
          name = "vision-system-dev";

          buildInputs = [
            pythonEnv
            pkgs.uv
            pkgs.pkg-config
          ] ++ runtimeLibs;

          shellHook = ''
            # Set up LD_LIBRARY_PATH so OpenCV wheels can find standard system libraries
            export LD_LIBRARY_PATH="${pkgs.lib.makeLibraryPath runtimeLibs}:$LD_LIBRARY_PATH"
            echo "VisionSystem development shell"
            echo "To sync dependencies and run:"
            echo "  uv sync --all-groups"
            echo "  uv run vision-localizer --help"
          '';
        };
      }
    );
}
