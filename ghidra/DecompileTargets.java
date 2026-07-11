import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.address.*;
import ghidra.util.task.ConsoleTaskMonitor;
import java.io.*;
import java.util.HashSet;

public class DecompileTargets extends GhidraScript {
    public void run() throws Exception {
        long[] targets = {0x4147e7L, 0x41563fL};
        String outpath = "/tmp/claude-1000/-home-legion-ds2fix/9d4373e1-afd8-495b-80f6-ecec804a7828/scratchpad/decomp.txt";
        DecompInterface di = new DecompInterface();
        di.openProgram(currentProgram);
        FunctionManager fm = currentProgram.getFunctionManager();
        AddressSpace space = currentProgram.getAddressFactory().getDefaultAddressSpace();
        ConsoleTaskMonitor mon = new ConsoleTaskMonitor();
        PrintWriter out = new PrintWriter(new FileWriter(outpath));
        HashSet<Long> seen = new HashSet<Long>();
        for (long t : targets) {
            Address addr = space.getAddress(t);
            Function fn = fm.getFunctionContaining(addr);
            if (fn == null) { out.println("\n==== NO FUNCTION containing 0x" + Long.toHexString(t) + " ===="); continue; }
            long ep = fn.getEntryPoint().getOffset();
            if (seen.contains(ep)) { out.println("\n==== (dup) 0x" + Long.toHexString(t) + " is in " + fn.getName() + " ===="); continue; }
            seen.add(ep);
            DecompileResults res = di.decompileFunction(fn, 90, mon);
            out.println("\n\n==================== " + fn.getName() + " @ 0x" + Long.toHexString(ep) + "  (target 0x" + Long.toHexString(t) + ") ====================");
            if (res != null && res.decompileCompleted()) out.println(res.getDecompiledFunction().getC());
            else out.println("<< decompile failed: " + (res != null ? res.getErrorMessage() : "null") + " >>");
        }
        out.close();
        println("WROTE " + outpath);
    }
}
