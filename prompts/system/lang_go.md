# Go-specific Review Guidelines

Focus on Go-specific concerns in addition to the general review:

**Error Handling**
- Ignored errors — every `err` return must be checked or explicitly discarded with `_`
- Error wrapping: use `fmt.Errorf("context: %w", err)` for wrappable errors
- Sentinel errors: prefer `errors.Is()` / `errors.As()` over `==`

**Concurrency**
- Data races: shared mutable state accessed from goroutines without synchronization
- Channel direction (`<-chan`, `chan<-`) missing in function signatures
- Goroutine leaks — goroutines started without a clear exit path
- Prefer `sync.WaitGroup` or `errgroup` over manual channel signaling

**Idiomatic Go**
- Named return values that add confusion (prefer explicit returns)
- Interface bloat — keep interfaces small (1-3 methods)
- `init()` functions with complex logic — prefer explicit initialization
- `panic` in library code (should be `error` instead)

**Performance**
- `strings.Builder` preferred over repeated `+` concatenation
- Preallocate slices with `make([]T, 0, capacity)` when size is known
- Value vs pointer receivers: consistency within a type's method set

**Context**
- Missing `context.Context` propagation in I/O calls
- Ignoring context cancellation in long-running loops
- `context.Background()` deep in call stacks (should be passed in)
