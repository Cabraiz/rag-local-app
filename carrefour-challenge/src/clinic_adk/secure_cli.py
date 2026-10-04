"""Security profile CLI, compatible with the existing CLI arguments."""
from .security_profile import install_runtime


def main():
    install_runtime()
    from .cli import main as cli_main
    return cli_main()


if __name__ == '__main__':
    raise SystemExit(main())
