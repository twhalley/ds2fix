import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.*;
import ghidra.program.model.address.*;
import ghidra.program.model.symbol.*;
import java.io.*;

// List call-sites (callers) of each address in DS2_TARGETS -> DS2_OUT.
public class Callers extends GhidraScript {
    public void run() throws Exception {
        String tgt = System.getenv("DS2_TARGETS");
        String outpath = System.getenv("DS2_OUT");
        if (outpath == null) outpath = "/tmp/ds2_callers.txt";
        PrintWriter out = new PrintWriter(new FileWriter(outpath));
        FunctionManager fm = currentProgram.getFunctionManager();
        ReferenceManager rm = currentProgram.getReferenceManager();
        AddressSpace space = currentProgram.getAddressFactory().getDefaultAddressSpace();
        for (String s : tgt.split(",")) {
            s = s.trim(); if (s.isEmpty()) continue;
            long t = Long.parseLong(s.replace("0x",""), 16);
            Address a = space.getAddress(t);
            out.println("\n==== callers of 0x" + Long.toHexString(t) + " ====");
            ReferenceIterator ri = rm.getReferencesTo(a);
            while (ri.hasNext()) {
                Reference r = ri.next();
                if (!r.getReferenceType().isCall() && !r.getReferenceType().isJump()) continue;
                Address from = r.getFromAddress();
                Function f = fm.getFunctionContaining(from);
                out.println("  " + from + "  " + r.getReferenceType()
                    + (f!=null?("   in "+f.getName()+" @0x"+Long.toHexString(f.getEntryPoint().getOffset())):""));
            }
        }
        out.close();
        println("WROTE " + outpath);
    }
}
