import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import ghidra.program.model.address.*;
import ghidra.program.model.mem.*;
import ghidra.program.util.*;
import java.io.*;
import java.util.regex.*;

public class FindUICanvas extends GhidraScript {
    public void run() throws Exception {
        String outpath = "/tmp/claude-1000/-home-legion-ds2fix/9d4373e1-afd8-495b-80f6-ecec804a7828/scratchpad/uicanvas.txt";
        PrintWriter out = new PrintWriter(new FileWriter(outpath));

        // 1) function names matching UI/resolution/canvas/screen patterns
        Pattern p = Pattern.compile("(?i)(UIShell|UICanvas|UIContext|Resolution|SetScreen|ScreenSize|OnResize|Resize|Canvas|SetRes|ScreenRes|Backbuffer|Present|Reset|DeviceReset|intended)");
        out.println("==== FUNCTIONS matching UI/resolution patterns ====");
        FunctionIterator fit = currentProgram.getFunctionManager().getFunctions(true);
        int c=0;
        while (fit.hasNext()) {
            Function f = fit.next();
            String n = f.getName();
            if (p.matcher(n).find()) { out.println(String.format("0x%08x  %s", f.getEntryPoint().getOffset(), n)); c++; }
        }
        out.println("(total "+c+")");

        // 2) find string "intended_resolution" and "%dx%dx%d" and xrefs
        out.println("\n==== string search + xrefs ====");
        String[] wanted = {"intended_resolution", "%dx%dx%d", "screen_width", "screen_height", "min_screen", "screen_rect"};
        Listing listing = currentProgram.getListing();
        DataIterator di = listing.getDefinedData(true);
        while (di.hasNext()) {
            Data d = di.next();
            if (d.hasStringValue()) {
                String s = d.getDefaultValueRepresentation();
                for (String w : wanted) {
                    if (s.toLowerCase().contains(w.toLowerCase())) {
                        out.println("STR @"+d.getAddress()+" = "+s);
                        ReferenceIterator ri = currentProgram.getReferenceManager().getReferencesTo(d.getAddress());
                        while (ri.hasNext()) {
                            Reference r = ri.next();
                            Function cf = currentProgram.getFunctionManager().getFunctionContaining(r.getFromAddress());
                            out.println("    xref from "+r.getFromAddress()+(cf!=null?("  in "+cf.getName()+" @0x"+Long.toHexString(cf.getEntryPoint().getOffset())):""));
                        }
                    }
                }
            }
        }
        out.close();
        println("WROTE "+outpath);
    }
}
