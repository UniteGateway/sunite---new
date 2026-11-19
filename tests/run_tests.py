"""Dependency-free runner for the engine test-suite (pytest also works)."""
import importlib, inspect, pathlib, sys, time, traceback
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
passed = failed = 0
for f in sorted(pathlib.Path(__file__).parent.glob("test_*.py")):
    try:
        mod = importlib.import_module(f"tests.{f.stem}")
    except ModuleNotFoundError as e:          # API tests need FastAPI; skip where it is not installed
        print(f"SKIP {f.stem} ({e.name} not installed)"); continue
    for name, fn in inspect.getmembers(mod, inspect.isfunction):
        if not name.startswith("test_") or fn.__module__ != mod.__name__:
            continue
        t = time.time()
        try:
            fn(); passed += 1; print(f"PASS {f.stem}::{name} ({time.time()-t:.2f}s)")
        except Exception:
            failed += 1; print(f"FAIL {f.stem}::{name}"); traceback.print_exc()
print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
