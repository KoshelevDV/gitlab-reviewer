# Rust-specific Review Guidelines

Focus on Rust-specific concerns in addition to the general review:

**Ownership & Borrowing**
- Unnecessary `.clone()` calls that could be avoided with proper lifetimes or restructuring
- Holding a borrow longer than needed, blocking other borrows
- Prefer `&str` over `&String`, `&[T]` over `&Vec<T>` in function parameters

**Error Handling**
- `unwrap()` / `expect()` in non-test code that could panic in production
- Missing `?` propagation — explicit `match` where `?` would be cleaner
- Error types that don't implement `std::error::Error`

**Safety**
- `unsafe` blocks: check that invariants are clearly documented
- `transmute` usage — almost always a red flag
- Raw pointer dereferencing without clear justification

**Performance**
- Unnecessary heap allocations (prefer stack allocation for small fixed-size types)
- String formatting in hot paths (use `write!` or pre-allocated buffers)
- Missing `#[inline]` on small frequently-called functions in library code

**Async (Tokio/async-std)**
- Blocking calls inside `async fn` (e.g., `std::thread::sleep`, sync I/O)
- Spawning tasks without handling `JoinHandle`
- Missing `select!` where fan-out/fan-in would be cleaner

**Idiomatic Rust**
- `match` vs `if let` — use `if let` for single-variant matches
- Iterator chains preferred over manual loops
- `Default::default()` where struct has a natural zero state
- Derive `Clone`, `Debug`, `PartialEq` where sensible
