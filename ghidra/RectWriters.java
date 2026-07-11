import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.*;
import ghidra.program.model.address.*;
import ghidra.program.model.symbol.*;
import java.io.*;
import java.util.*;

// Find functions that reference global DS2_GLOBAL (hex) and, nearby, write to a rect
// field [reg+0xac/0xb0/0xb4/0xb8]. Also list all xrefs to the global.
public class RectWriters extends GhidraScript {
    public void run() throws Exception {
        long g = Long.parseLong(System.getenv("DS2_GLOBAL").replace("0x",""),16);
        String outpath = System.getenv("DS2_OUT");
        if (outpath == null) outpath = "/tmp/ds2_rect.txt";
        PrintWriter out = new PrintWriter(new FileWriter(outpath));
        FunctionManager fm = currentProgram.getFunctionManager();
        ReferenceManager rm = currentProgram.getReferenceManager();
        Listing lst = currentProgram.getListing();
        Address ga = currentProgram.getAddressFactory().getDefaultAddressSpace().getAddress(g);

        out.println("==== xrefs to 0x"+Long.toHexString(g)+" ====");
        TreeSet<String> funcs = new TreeSet<String>();
        ReferenceIterator ri = rm.getReferencesTo(ga);
        while (ri.hasNext()) {
            Reference r = ri.next();
            Function f = fm.getFunctionContaining(r.getFromAddress());
            out.println("  "+r.getFromAddress()+"  "+r.getReferenceType()
                +(f!=null?("   in "+f.getName()+" @0x"+Long.toHexString(f.getEntryPoint().getOffset())):""));
            if (f!=null) funcs.add("0x"+Long.toHexString(f.getEntryPoint().getOffset())+" "+f.getName());
        }
        out.println("\n==== distinct functions referencing it ====");
        for (String s: funcs) out.println("  "+s);

        // scan whole program for stores to +0xb4 / +0xb8 / +0xac / +0xb0 (the rect fields)
        out.println("\n==== stores to a rect field +0xac/+0xb0/+0xb4/+0xb8 (right/bottom of a window rect) ====");
        InstructionIterator it = lst.getInstructions(true);
        while (it.hasNext()) {
            Instruction ins = it.next();
            String m = ins.toString();
            if (!m.startsWith("MOV")) continue;
            int comma = m.indexOf(',');
            String dst = comma>0? m.substring(0,comma):m;
            if (!dst.contains("[")) continue;
            if (dst.contains("0xb4]")||dst.contains("0xb8]")) {
                Function f = fm.getFunctionContaining(ins.getAddress());
                out.println("  "+ins.getAddress()+"  "+m
                    +(f!=null?("   in "+f.getName()+" @0x"+Long.toHexString(f.getEntryPoint().getOffset())):""));
            }
        }
        out.close();
        println("WROTE "+outpath);
    }
}
