import json
import os

prog = currentProgram  # noqa: F821 - injected by Ghidra / Jython
fm = prog.getFunctionManager()

out = {
    "program": str(prog.getName()),
    "executable_path": str(prog.getExecutablePath()),
    "language": str(prog.getLanguageID()),
    "compiler": str(prog.getCompilerSpec().getCompilerSpecID()),
    "image_base": str(prog.getImageBase()),
    "min_address": str(prog.getMinAddress()),
    "max_address": str(prog.getMaxAddress()),
    "function_count": fm.getFunctionCount(),
    "external_symbols": [],
    "functions": [],
    "memory_blocks": [],
}

for s in prog.getSymbolTable().getExternalSymbols():
    out["external_symbols"].append(str(s.getName()))

for f in fm.getFunctions(True):
    if len(out["functions"]) >= 100:
        break
    out["functions"].append({
        "name": str(f.getName()),
        "entry": str(f.getEntryPoint()),
        "size": int(f.getBody().getNumAddresses()),
        "is_thunk": bool(f.isThunk()),
    })

for b in prog.getMemory().getBlocks():
    out["memory_blocks"].append({
        "name": str(b.getName()),
        "start": str(b.getStart()),
        "size": int(b.getSize()),
        "r": bool(b.isRead()),
        "w": bool(b.isWrite()),
        "x": bool(b.isExecute()),
    })

path = os.environ.get("GHIDRA_OUT", "/tmp/ghidra-summary.json")
fh = open(path, "w")
fh.write(json.dumps(out, indent=2, sort_keys=True))
fh.close()
print("[rea-remote] wrote " + path)
