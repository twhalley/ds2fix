import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import ghidra.program.model.address.*;
import ghidra.program.model.mem.MemoryAccessException;
import ghidra.util.task.ConsoleTaskMonitor;
import java.io.*;
import java.util.*;

// Find the UIShell resolution setter: a function that writes BOTH [base+0x78] and
// [base+0x7c] (width & height together) -- far stronger signal than +0x78 alone.
public class FindSetter2 extends GhidraScript {
    public void run() throws Exception {
        String outpath = System.getenv("DS2_OUT");
        if (outpath == null) outpath = "/tmp/ds2_setter.txt";
        PrintWriter out = new PrintWriter(new FileWriter(outpath));

        Listing lst = currentProgram.getListing();
        FunctionManager fm = currentProgram.getFunctionManager();

        // Pass 1: per-function, collect displacement writes to +0x78 and +0x7c.
        // A resolution setter touches both in the same function.
        HashMap<Function,int[]> hits = new HashMap<Function,int[]>(); // [wrote78, wrote7c]
        HashMap<Function,ArrayList<String>> ctx = new HashMap<Function,ArrayList<String>>();
        InstructionIterator it = lst.getInstructions(true);
        while (it.hasNext()) {
            Instruction ins = it.next();
            String m = ins.toString();
            boolean isStore = m.startsWith("MOV") && m.contains("[") && m.contains("=>") == false;
            // We only care about writes: dest is the memory operand (operand 0 has '[')
            if (!m.startsWith("MOV")) continue;
            boolean has78 = m.contains("+ 0x78]") || m.contains("+0x78]");
            boolean has7c = m.contains("+ 0x7c]") || m.contains("+0x7c]");
            if (!has78 && !has7c) continue;
            // require the memory operand to be operand 0 (a store), i.e. text before first ',' has '['
            int comma = m.indexOf(',');
            String dst = comma>0 ? m.substring(0, comma) : m;
            boolean storeToField = dst.contains("[") && (dst.contains("0x78]") || dst.contains("0x7c]"));
            if (!storeToField) continue;
            Function f = fm.getFunctionContaining(ins.getAddress());
            if (f == null) continue;
            int[] hv = hits.get(f);
            if (hv == null) { hv = new int[2]; hits.put(f, hv); ctx.put(f, new ArrayList<String>()); }
            if (dst.contains("0x78]")) hv[0]++;
            if (dst.contains("0x7c]")) hv[1]++;
            ctx.get(f).add(ins.getAddress() + "  " + m);
        }

        out.println("==== functions writing BOTH +0x78 AND +0x7c (candidate resolution setters) ====");
        for (Map.Entry<Function,int[]> e : hits.entrySet()) {
            int[] hv = e.getValue();
            if (hv[0] > 0 && hv[1] > 0) {
                Function f = e.getKey();
                out.println("\n-- " + f.getName() + " @0x" + Long.toHexString(f.getEntryPoint().getOffset())
                        + "  (78x" + hv[0] + " 7cx" + hv[1] + ")");
                for (String s : ctx.get(f)) out.println("   " + s);
            }
        }

        out.println("\n\n==== functions writing +0x78 ONLY or +0x7c ONLY (secondary) ====");
        for (Map.Entry<Function,int[]> e : hits.entrySet()) {
            int[] hv = e.getValue();
            if ((hv[0] > 0) ^ (hv[1] > 0)) {
                Function f = e.getKey();
                out.println("-- " + f.getName() + " @0x" + Long.toHexString(f.getEntryPoint().getOffset())
                        + "  (78x" + hv[0] + " 7cx" + hv[1] + ")");
            }
        }

        // Pass 2: every xref to the UIShell global pointer 0xbcb2d4, with containing function.
        out.println("\n\n==== xrefs to UIShell global 0xbcb2d4 ====");
        Address shell = currentProgram.getAddressFactory().getDefaultAddressSpace().getAddress(0xbcb2d4L);
        ReferenceManager rm = currentProgram.getReferenceManager();
        ReferenceIterator ri = rm.getReferencesTo(shell);
        while (ri.hasNext()) {
            Reference r = ri.next();
            Address from = r.getFromAddress();
            Function f = fm.getFunctionContaining(from);
            out.println(from + "  " + r.getReferenceType()
                    + (f!=null?("   in "+f.getName()+" @0x"+Long.toHexString(f.getEntryPoint().getOffset())):""));
        }

        out.close();
        println("WROTE " + outpath);
    }
}
