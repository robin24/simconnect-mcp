"""MCP prompt templates for SimConnect development tasks."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP


def register_prompts(mcp: FastMCP) -> None:
    """Register prompt templates on the MCP server."""

    @mcp.prompt()
    def debug_simvar(simvar_name: str, expected_behavior: str = "") -> str:
        """Guide debugging a misbehaving SimVar.

        Args:
            simvar_name: The SimVar that isn't working correctly
            expected_behavior: What you expected to happen
        """
        return f"""You are debugging a SimConnect SimVar issue.

**SimVar:** `{simvar_name}`
**Expected behavior:** {expected_behavior or "Not specified"}

Follow this debugging procedure:

1. **Verify the variable exists:** Use `msfs_search_simvars("{simvar_name}")` to confirm the name and check its properties (units, settable, category).

2. **Read the current value:** Use `msfs_get_simvar("{simvar_name}")` to see what the sim is returning right now.

3. **Check units:** SimVars can return different values depending on the unit requested. Try reading with different units if the value seems wrong.

4. **Check if it's indexed:** Some SimVars (like engine vars) require an index parameter. Check if `:index` appears in the variable name.

5. **Monitor over time:** Use `msfs_watch_simvar("{simvar_name}")` to see if the value changes as expected.

6. **For settable vars:** Verify the variable is marked as settable. Not all SimVars accept writes.

7. **Check the documentation:** Read `simconnect://docs/simvars/all` for details on this variable.

8. **Common pitfalls:**
   - Some vars only update when the relevant system is powered
   - Some vars require specific sim conditions (in-flight vs on-ground)
   - String vars (like TITLE) may need special handling
   - Indexed vars start at index 1, not 0
"""

    @mcp.prompt()
    def analyze_aircraft_vars() -> str:
        """Enumerate and categorize all L-vars on the current aircraft."""
        return """You are analyzing the L-vars available on the currently loaded aircraft.

Follow this procedure:

1. **Check MobiFlight availability:** Use `msfs_get_connection_status()` to verify
   MobiFlight is loaded.

2. **List all L-vars:** Use `msfs_list_lvars()` to get every L-var registered by this aircraft.

3. **Categorize the variables** by examining their naming patterns:
   - Group by prefix (e.g., `A32NX_`, `WT_CJ4_`, `AS1000_`)
   - Identify systems: EFIS, FCU, autopilot, engines, electrical, hydraulics, etc.
   - Note which appear to be boolean (switches) vs continuous values

4. **Sample key values:** Read a representative variable from each category with `msfs_get_lvar()`.

5. **Present a summary** organized by system, noting:
   - Total L-var count
   - Major systems exposed
   - Which variables appear writable/controllable
   - Any naming conventions the aircraft uses
"""

    @mcp.prompt()
    def create_addon_boilerplate(addon_type: str = "gauge") -> str:
        """Generate boilerplate code for an MSFS add-on.

        Args:
            addon_type: Type of add-on (gauge, instrument, panel, module)
        """
        return f"""Generate starter boilerplate code for an MSFS add-on of type: **{addon_type}**

Consider the following:

1. **SimConnect integration:** Include the basic SimConnect connection setup, data definitions, and event handling.

2. **Best practices:** Follow the patterns from `simconnect://docs/best-practices`:
   - Proper connection lifecycle management
   - Efficient data request patterns (avoid polling too frequently)
   - Error handling for connection loss
   - Thread safety considerations

3. **Variable access patterns:** Show examples of:
   - Reading SimVars with appropriate units
   - Subscribing to data changes
   - Triggering events
   - L-var access (if applicable for this add-on type)

4. **Project structure:** Suggest a project layout appropriate for the add-on type.

5. **Testing approach:** Include guidance on how to test the add-on with the SimConnect MCP server.

Refer to `simconnect://docs/overview` for SimConnect architecture context.
"""

    @mcp.prompt()
    def rpn_helper(description: str) -> str:
        """Translate a natural language description into RPN calculator code.

        Args:
            description: What the calculator code should do
        """
        return f"""You are an RPN (Reverse Polish Notation) calculator code expert for MSFS.

**Task:** {description}

Write RPN calculator code that accomplishes this task. Follow these guidelines:

1. **RPN syntax reference** (from `simconnect://docs/rpn`):
   - Read SimVar: `(A:VAR_NAME, unit)`
   - Read L-var: `(L:VAR_NAME)`
   - Write L-var: `value (>L:VAR_NAME)`
   - Trigger K-event: `value (>K:EVENT_NAME)`
   - Arithmetic: `a b +`, `a b -`, `a b *`, `a b /`
   - Comparison: `a b ==`, `a b >`, `a b <`
   - Conditional: `condition if{{ true_code }} els{{ false_code }}`
   - Stack ops: `dup`, `swap`, `pop`

2. **Explain each step** of the RPN code.

3. **Test the code** by running `msfs_execute_calculator_code()` with the generated code.

4. **Verify the result** by reading the affected variables after execution.
"""

    @mcp.prompt()
    def simconnect_code_review(code: str = "") -> str:
        """Review SimConnect code for common issues.

        Args:
            code: The code to review (or describe what to look at)
        """
        return f"""You are reviewing SimConnect integration code for common issues.

{"**Code to review:**" + chr(10) + "```" + chr(10) + code + chr(10) + "```" if code else "Ask the user to share the code they want reviewed."}

Check for these common SimConnect issues:

1. **Connection management:**
   - Is the connection properly initialized and cleaned up?
   - Is there reconnection logic for dropped connections?
   - Is `SimConnect.exit()` called on shutdown?

2. **Thread safety:**
   - SimConnect DLL calls are NOT thread-safe — is there a lock?
   - Are blocking SimConnect calls running on a background thread?

3. **Data requests:**
   - Are data definitions properly registered before requesting?
   - Is the polling interval reasonable (not too fast)?
   - Are unused data requests cleaned up?

4. **Event handling:**
   - Are events properly mapped before triggering?
   - Are event parameters in the correct range?

5. **Performance:**
   - Avoid requesting all SimVars every frame
   - Use `_time` parameter in AircraftRequests for caching
   - Batch related reads together

6. **Error handling:**
   - SimConnect can throw OSError on connection loss
   - Check for None/invalid values from reads
   - Handle sim pause/resume gracefully

Refer to `simconnect://docs/best-practices` for detailed guidance.
"""
