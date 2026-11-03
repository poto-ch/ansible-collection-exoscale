{
  pkgs,
  config,
  ...
}:

let
  # nixpkgs ships no `exoscale` SDK. Ansible only imports from the interpreter it
  # was built with, so the SDK goes into that same python env — a shell-level
  # PYTHONPATH would be invisible to `ansible-inventory`.
  requests-exoscale-auth = pkgs.python3Packages.buildPythonPackage rec {
    pname = "requests-exoscale-auth";
    version = "1.1.2";
    pyproject = true;
    build-system = [ pkgs.python3Packages.setuptools ];
    src = pkgs.fetchPypi {
      inherit pname version;
      hash = "sha256-mZlkT1EUNULUUm0b7vjJXUmH5EGyhemLo1NLJ6jw0vE=";
    };
    dependencies = [ pkgs.python3Packages.requests ];
    doCheck = false;
  };

  exoscale = pkgs.python3Packages.buildPythonPackage rec {
    pname = "exoscale";
    version = "0.16.4";
    pyproject = true;
    build-system = [ pkgs.python3Packages.hatchling ];
    src = pkgs.fetchPypi {
      inherit pname version;
      hash = "sha256-Uv4m03tpPYV1GesNvfTIUUx/Pw+zEAi86RS4oDkVazw=";
    };
    dependencies = [
      pkgs.python3Packages.requests
      requests-exoscale-auth
    ];
    doCheck = false;
    pythonImportsCheck = [ "exoscale.api.v2" ];
  };

  python = pkgs.python3.withPackages (ps: [
    ps.ansible-core
    ps.pytest
    ps.pytest-mock
    ps.pytest-xdist
    ps.requests-mock
    exoscale
  ]);

  # `ansible-test` and `import ansible_collections.poto.exoscale.…` both need
  # the checkout to live at ansible_collections/<ns>/<name>.
  collectionsRoot = config.devenv.state;
  collectionDir = "${collectionsRoot}/ansible_collections/poto/exoscale";
in
{
  languages.ansible = {
    enable = true;
    package = python;
  };

  packages = with pkgs; [
    prek
    ruff
    yamllint
  ];

  env = {
    ANSIBLE_COLLECTIONS_PATH = collectionsRoot;
    PYTHONPATH = collectionsRoot;
    LC_ALL = "C.UTF-8";
  };
  # ansible-lint's wrapper puts a bare python and community ansible ahead of our
  # env on PATH. ansible-test resolves the interpreter by versioned name and
  # sanitises PYTHONPATH, so from there it would see neither the SDK nor pytest.
  enterShell = ''
    export PATH="${python}/bin:$PATH"
  '';

  tasks."collection:link" = {
    exec = ''
      mkdir -p "${dirOf collectionDir}"
      ln -sfn "${config.devenv.root}" "${collectionDir}"
    '';
    status = ''test "$(readlink "${collectionDir}")" = "${config.devenv.root}"'';
    before = [ "devenv:enterShell" ];
  };

  scripts = {
    test = {
      exec = "pytest -q tests/unit";
      description = "Run the inventory plugin unit tests";
    };
    doc = {
      exec = "ansible-doc -t inventory poto.exoscale.exoscale";
      description = "Render the plugin documentation";
    };
    build = {
      exec = "ansible-galaxy collection build --force";
      description = "Build the collection tarball";
    };
  };

  git-hooks = {
    package = pkgs.prek;
    hooks = {
      trim-trailing-whitespace.enable = true;
      end-of-file-fixer.enable = true;
      mixed-line-endings = {
        enable = true;
        args = [ "--fix=lf" ];
      };
      check-yaml.enable = true;
      yamllint = {
        enable = true;
        settings.preset = "relaxed";
      };
      ruff.enable = true;
      ruff-format.enable = true;
      ansible-lint.enable = true;
    };
  };
}
