from pkgutil import ModuleInfo, iter_modules


EXTENSIONS: list[ModuleInfo] = [
    module for module in iter_modules(__path__, f"{__package__}.") if not module.name.startswith(f"{__package__}._")
]
