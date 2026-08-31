# RPN Calculator Code Reference

MSFS includes a stack-based RPN (Reverse Polish Notation) calculator engine that can read/write any variable type, perform arithmetic, and execute conditional logic.

## Basic Syntax

RPN uses a **stack**. Values are pushed onto the stack, and operators pop values and push results.

```
3 4 +       → pushes 3, pushes 4, adds them → result: 7
10 3 -      → 10 minus 3 → result: 7
5 6 * 2 /   → (5 × 6) / 2 → result: 15
```

## Reading Variables

### SimVars (A: variables)
```
(A:PLANE_ALTITUDE, feet)
(A:AIRSPEED_INDICATED, knots)
(A:GENERAL ENG THROTTLE LEVER POSITION:1, percent)
```

### L-Vars (L: variables)
```
(L:MyCustomVariable)
(L:A32NX_EFIS_L_OPTION)
```

### Environment Variables (E:)
```
(E:SIMULATION TIME, seconds)
(E:ZULU TIME, seconds)
```

## Writing Variables

### Write to L-Var
```
42 (>L:MyCustomVariable)
```

### Trigger K-Event
```
1 (>K:PARKING_BRAKES)
16383 (>K:THROTTLE_SET)
```

### Write to SimVar (limited support)
```
35000 (>A:AUTOPILOT ALTITUDE LOCK VAR, feet)
```

## Arithmetic Operators

| Operator | Description | Example |
|----------|-------------|---------|
| `+` | Addition | `3 4 +` → 7 |
| `-` | Subtraction | `10 3 -` → 7 |
| `*` | Multiplication | `5 6 *` → 30 |
| `/` | Division | `15 3 /` → 5 |
| `%` | Modulo | `10 3 %` → 1 |
| `abs` | Absolute value | `-5 abs` → 5 |
| `neg` | Negate | `5 neg` → -5 |
| `int` | Truncate to integer | `3.7 int` → 3 |
| `flr` | Floor | `3.7 flr` → 3 |
| `cel` | Ceiling | `3.2 cel` → 4 |
| `rnd` | Round | `3.5 rnd` → 4 |
| `min` | Minimum | `3 5 min` → 3 |
| `max` | Maximum | `3 5 max` → 5 |

## Comparison Operators

| Operator | Description | Example |
|----------|-------------|---------|
| `==` | Equal | `3 3 ==` → 1 (true) |
| `!=` | Not equal | `3 4 !=` → 1 |
| `>` | Greater than | `5 3 >` → 1 |
| `<` | Less than | `3 5 <` → 1 |
| `>=` | Greater or equal | `5 5 >=` → 1 |
| `<=` | Less or equal | `3 5 <=` → 1 |

## Logical Operators

| Operator | Description |
|----------|-------------|
| `and` | Logical AND |
| `or` | Logical OR |
| `not` | Logical NOT |

## Stack Operations

| Operator | Description |
|----------|-------------|
| `dup` | Duplicate top of stack |
| `swap` | Swap top two values |
| `pop` | Remove top of stack |

## Conditional Execution

```
condition if{ true_code } els{ false_code }
```

### Examples

Toggle an L-var between 0 and 1:
```
(L:MySwitch) 0 == if{ 1 (>L:MySwitch) } els{ 0 (>L:MySwitch) }
```

Clamp altitude to a range:
```
(A:PLANE_ALTITUDE, feet) 1000 max 40000 min
```

Increment a counter:
```
(L:MyCounter) 1 + (>L:MyCounter)
```

## Common Patterns

### Read, modify, write
```
(L:SomeValue) 10 + (>L:SomeValue)
```

### Conditional event trigger
```
(A:SIM ON GROUND, bool) if{ 1 (>K:PARKING_BRAKES) }
```

### Unit conversion in-line
```
(A:PLANE_ALTITUDE, meters) 3.28084 *
```

### Multi-step calculation
```
(A:AIRSPEED_INDICATED, knots) (A:AMBIENT_TEMPERATURE, celsius) 273.15 +
```

## Tips

- Variable names in RPN use **spaces** not underscores: `PLANE ALTITUDE` not `PLANE_ALTITUDE`
- Always specify units for A: variables
- L: variables don't use units
- The calculator runs synchronously in the sim's gauge update loop
- Use `msfs_execute_calculator_code()` to run RPN from the MCP server
