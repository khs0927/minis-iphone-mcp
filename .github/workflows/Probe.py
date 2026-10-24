import java.io
import os

f = java.io.File("/tmp/ghidra-probe-ok.txt")
f.createNewFile()

path = os.environ.get("GHIDRA_OUT", "/tmp/ghidra-summary.json")
fh = open(path, "w")
fh.write('{"probe": true, "ghidra_headless": "ok", "script_discovered": true}\n')
fh.close()
print("[rea-remote] ghidra probe script ran OK -> " + path)
