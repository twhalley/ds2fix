import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.address.*;
import ghidra.program.model.symbol.*;
import ghidra.util.task.ConsoleTaskMonitor;
import java.io.*;
import java.util.*;

// For each data VA in DS2_ADDRS, list xrefs (referencing instr + containing fn), then decompile
// each unique containing function to DS2_OUT.
public class XChase extends GhidraScript {
    public void run() throws Exception {
        String tgt = System.getenv("DS2_ADDRS");
        String outpath = System.getenv("DS2_OUT");
        if (outpath == null) outpath = "/tmp/ds2_xchase.txt";
        DecompInterface di = new DecompInterface();
        di.openProgram(currentProgram);
        FunctionManager fm = currentProgram.getFunctionManager();
        ReferenceManager rm = currentProgram.getReferenceManager();
        AddressSpace space = currentProgram.getAddressFactory().getDefaultAddressSpace();
        ConsoleTaskMonitor mon = new ConsoleTaskMonitor();
        PrintWriter out = new PrintWriter(new FileWriter(outpath));
        LinkedHashSet<Long> fns = new LinkedHashSet<Long>();
        for (String s : tgt.split(",")) {
            s = s.trim(); if (s.isEmpty()) continue;
            long t = Long.parseLong(s.replace("0x",""), 16);
            Address a = space.getAddress(t);
            out.println("\n===== xrefs to 0x" + Long.toHexString(t) + " =====");
            ReferenceIterator it = rm.getReferencesTo(a);
            int cnt = 0;
            while (it.hasNext()) {
                Reference r = it.next(); Address from = r.getFromAddress();
                Function fn = fm.getFunctionContaining(from);
                String fname = fn == null ? "<none>" : (fn.getName() + " @0x" + Long.toHexString(fn.getEntryPoint().getOffset()));
                out.println("  from 0x" + Long.toHexString(from.getOffset()) + "  in " + fname + "  [" + r.getReferenceType() + "]");
                if (fn != null) fns.add(fn.getEntryPoint().getOffset());
                cnt++;
            }
            if (cnt == 0) out.println("  (none)");
        }
        for (Long ep : fns) {
            Function fn = fm.getFunctionAt(space.getAddress(ep));
            if (fn == null) continue;
            DecompileResults res = di.decompileFunction(fn, 120, mon);
            out.println("\n\n==================== " + fn.getName() + " @ 0x" + Long.toHexString(ep) + " ====================");
            if (res != null && res.decompileCompleted()) out.println(res.getDecompiledFunction().getC());
            else out.println("<< decompile failed >>");
        }
        out.close();
        println("WROTE " + outpath);
    }
}
