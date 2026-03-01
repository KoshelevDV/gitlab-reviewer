# TypeScript/JavaScript-specific Review Guidelines

Focus on TypeScript/JavaScript-specific concerns in addition to the general review:

**Type Safety**
- `any` type usage — request explicit types or `unknown` with narrowing
- Non-null assertions (`!`) without justification
- Missing return type annotations on exported functions
- Loose equality (`==`) instead of strict (`===`)

**Async Patterns**
- Floating promises — `async` calls without `await` or `.catch()`
- Missing error handling in `.then()` chains
- `Promise.all` missed where parallel execution is possible
- `await` inside loops that could use `Promise.all`

**React (if applicable)**
- Missing dependency arrays in `useEffect` / `useMemo` / `useCallback`
- State mutation instead of returning new objects
- Key prop missing or using array index as key in dynamic lists
- Large components that should be extracted

**Security**
- `dangerouslySetInnerHTML` without sanitization
- Dynamic `require()` or `import()` with user-controlled paths
- Template literals used for SQL/shell without parameterization

**Performance**
- Re-creating objects/arrays in render (should be memoized or hoisted)
- Synchronous blocking in Node.js event loop
- Missing `React.memo` / `useMemo` for expensive computations

**Code Quality**
- `console.log` left in production code
- Overly deep callback nesting (prefer async/await)
- Missing optional chaining (`?.`) where null checks are verbose
