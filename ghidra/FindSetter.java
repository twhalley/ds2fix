import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import ghidra.program.model.address.*;
import ghidra.program.model.scalar.Scalar;
import ghidra.util.task.ConsoleTaskMonitor;
import java.io.*;

public class FindSetter extends GhidraScript {
    public void run() throws Exception {
        String outpath = "/tmp/claude-1000/-home-legion-ds2fix/9d4373e1-afd8-495b-80f6-ecec804a7828/scratchpad/setter.txt";
        PrintWriter out = new PrintWriter(new FileWriter(outpath));

        // 1) decompile getters by name to confirm offset
        DecompInterface di = new DecompInterface();
        di.openProgram(currentProgram);
        ConsoleTaskMonitor mon = new ConsoleTaskMonitor();
        String[] names = {"GetScreenWidth", "GetScreenHeight", "GetScreenRect"};
        SymbolTable st = currentProgram.getSymbolTable();
        for (String nm : names) {
            SymbolIterator si = st.getSymbols(nm);
            while (si.hasNext()) {
                Symbol s = si.next();
                Function f = currentProgram.getFunctionManager().getFunctionAt(s.getAddress());
                if (f == null) continue;
                DecompileResults r = di.decompileFunction(f, 60, mon);
                out.println("\n==== " + f.getName() + " @ " + f.getEntryPoint() + " ====");
                if (r != null && r.decompileCompleted()) out.println(r.getDecompiledFunction().getC());
            }
        }

        // 2) scan instructions: write to [reg + 0x78] where reg was loaded from the UIShell global 0xbcb2d4
        out.println("\n\n==== writes to [UIShell(0xbcb2d4)+0x78 / +0x7c] ====");
        long UISHELL = 0xbcb2d4L;
        Listing lst = currentProgram.getListing();
        InstructionIterator it = lst.getInstructions(true);
        // track: last register that got loaded from 0xbcb2d4, reset heuristically
        java.util.HashMap<String,Long> regFromShell = new java.util.HashMap<String,Long>();
        Instruction prev = null;
        int count = 0;
        InstructionIterator it2 = lst.getInstructions(true);
        java.util.ArrayList<Instruction> window = new java.util.ArrayList<Instruction>();
        while (it2.hasNext()) {
            Instruction ins = it2.next();
            window.add(ins);
            if (window.size() > 14) window.remove(0);
            String m = ins.toString();
            // detect a store to [X + 0x78] or [X + 0x7c]
            if (m.matches(".*\\+0x7[8c)].*") && m.startsWith("MOV") && m.contains("[")) {
                // check window for a load of 0xbcb2d4
                boolean shell = false;
                for (Instruction w : window) {
                    String ws = w.toString();
                    if (ws.contains("0xbcb2d4")) { shell = true; break; }
                }
                if (shell) {
                    Function cf = currentProgram.getFunctionManager().getFunctionContaining(ins.getAddress());
                    out.println(ins.getAddress() + "  " + m + (cf!=null?("   in "+cf.getName()+" @0x"+Long.toHexString(cf.getEntryPoint().getOffset())):""));
                    count++;
                }
            }
        }
        out.println("(store-near-UIShell matches: " + count + ")");
        out.close();
        println("WROTE " + outpath);
    }
}
