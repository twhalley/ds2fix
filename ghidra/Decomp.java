import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.address.*;
import ghidra.util.task.ConsoleTaskMonitor;
import java.io.*;
import java.util.HashSet;

// Decompile a comma-separated list of addresses from DS2_TARGETS, write to DS2_OUT.
public class Decomp extends GhidraScript {
    public void run() throws Exception {
        String tgt = System.getenv("DS2_TARGETS");
        String outpath = System.getenv("DS2_OUT");
        if (outpath == null) outpath = "/tmp/ds2_decomp.txt";
        DecompInterface di = new DecompInterface();
        di.openProgram(currentProgram);
        FunctionManager fm = currentProgram.getFunctionManager();
        AddressSpace space = currentProgram.getAddressFactory().getDefaultAddressSpace();
        ConsoleTaskMonitor mon = new ConsoleTaskMonitor();
        PrintWriter out = new PrintWriter(new FileWriter(outpath));
        HashSet<Long> seen = new HashSet<Long>();
        for (String s : tgt.split(",")) {
            s = s.trim();
            if (s.isEmpty()) continue;
            long t = Long.parseLong(s.replace("0x",""), 16);
            Address addr = space.getAddress(t);
            Function fn = fm.getFunctionContaining(addr);
            if (fn == null) { out.println("\n==== NO FUNCTION at 0x" + Long.toHexString(t) + " ===="); continue; }
            long ep = fn.getEntryPoint().getOffset();
            if (seen.contains(ep)) continue;
            seen.add(ep);
            DecompileResults res = di.decompileFunction(fn, 120, mon);
            out.println("\n\n==================== " + fn.getName() + " @ 0x" + Long.toHexString(ep) + " ====================");
            if (res != null && res.decompileCompleted()) out.println(res.getDecompiledFunction().getC());
            else out.println("<< decompile failed: " + (res != null ? res.getErrorMessage() : "null") + " >>");
        }
        out.close();
        println("WROTE " + outpath);
    }
}
