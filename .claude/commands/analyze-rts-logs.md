# RTS Log Analyzer Command

Analyze OSS-Fuzz incremental build and RTS (Regression Test Selection) test logs in the current directory.

> **Note:** This command uses the `rts-log-analyzer` skill. The skill provides detailed analysis patterns and workflows.

## Quick Start

Analyze all RTS test logs in the current directory to identify:
- Build failures
- Test failures with **specific error details**
- Failed test classes and methods
- Exception types and error messages
- Stack traces and source locations

## CRITICAL: Detailed Error Reporting

**DO NOT** report generic summaries like:
- "Test failures in baseline"
- "Flaky tests need to be skipped"
- "docker start command has failed"

**YOU MUST** report specific details:
- Exact test method names that failed
- Exception types (e.g., `IllegalAccessException`, `UnsatisfiedLinkError`)
- Error messages (e.g., `cannot open shared object file: libfreetype.so.6`)
- Source file and line numbers when available

## Instructions

Use the `rts-log-analyzer` skill to perform comprehensive log analysis.

### Analysis Steps:

1. **Check Summary** - Read `summary.txt` for overall pass/fail status
2. **Categorize Errors** - Group by specific error type
3. **Extract Details** - Get specific error messages, failed test methods, and exception types
4. **Include Stack Traces** - Show relevant stack trace snippets
5. **Generate Detailed Report** - Provide actionable information per project

### Key grep patterns to use:

```bash
# Build failures
grep -l "BUILD FAILURE" *.log

# Test failures with class names
grep -E "FAILURE! - in" *.log

# OSS-Patch errors
grep -oP "OSS-Patch \| ERROR \| .*" *.log | sort -u

# Failed test methods with error context
grep -A5 "<<< FAILURE!" <logfile>
grep -A5 "<<< ERROR!" <logfile>

# Exception types
grep -E "^(java\.|org\.)[a-zA-Z.]+Exception:|^[a-zA-Z.]+Error:" <logfile>

# Missing library errors
grep -B2 -A3 "cannot open shared object|libfreetype" <logfile>

# JDK module access errors
grep -B1 -A3 "InaccessibleObjectException|IllegalAccessException" <logfile>

# Docker start failures - LOOK ABOVE for actual error
grep -B30 "docker start command has failed" <logfile> | grep -E "\[ERROR\]|Exception|FAILURE"

# RAT license check (common docker start failure cause)
grep -B20 "docker start command has failed" <logfile> | grep -i "RatCheckException\|unapproved license"

# Surefire version error (common docker start failure cause)
grep -B20 "docker start command has failed" <logfile> | grep -i "Not supported surefire version"

# Missing pom.xml (common docker start failure cause)
grep -B20 "docker start command has failed" <logfile> | grep -i "No pom.xml"
```

### Output Format

For each failed project, report with THIS level of detail:

```
## [Project Name]

**Error Type:** <specific classification>

**Failed Tests:**
| Test Class | Test Method | Exception | Error Message |
|------------|-------------|-----------|---------------|
| ClassUtilTest | testFindEnumType | IllegalAccessException | cannot access member of java.util.EnumSet |
| ZKUtilTest | testUnreadableFileInput | AssertionFailedError | expected: not <null> |

**Stack Trace Snippet:**
```
java.lang.IllegalAccessException: class X cannot access a member of class Y
    at java.base/jdk.internal.reflect.Reflection.newIllegalAccessException(...)
```

**Root Cause:** <specific actionable cause>
**Suggested Fix:** <concrete fix recommendation>
```

### BAD Output Examples (DO NOT DO THIS):

```
| atlanta-jackson-databind-delta-01 | Test failures in baseline |
| atlanta-olingo-delta-01 | docker start command has failed |
```

### GOOD Output Example (Test Failures):

```
## atlanta-jackson-databind-delta-01

**Error Type:** JDK Module Access Restrictions

**Failed Tests (5 tests):**
| Test Class | Test Method | Exception |
|------------|-------------|-----------|
| ClassUtilTest | testFindEnumType | IllegalAccessException |
| StackTraceElementTest | testCustomStackTraceDeser | InvalidDefinitionException |
| ClassNameIdResolverTest | initializationError | ObjenesisException |
| ArrayDelegatorCreatorForCollectionTest | testUnmodifiable | InaccessibleObjectException |
| TestTypeFactoryWithClassLoader | initializationError | ObjenesisException |

**Root Cause:** Tests require reflective access to JDK internal modules
**Suggested Fix:** Add `--add-opens java.base/java.util=ALL-UNNAMED` to surefire JVM args or skip these tests
```

### GOOD Output Example (Docker Start Failures):

```
## atlanta-olingo-delta-01

**Error Type:** RAT License Check Failure (during docker start)

**Error Details:**
- Plugin: `org.apache.rat:apache-rat-plugin`
- Exception: `RatCheckException`
- Message: `Too many files with unapproved license: 1`
- Report: `/built-src/src/cp-java-olingo-src/target/rat.txt`

**Root Cause:** Source files missing Apache license headers
**Suggested Fix:** Add `-Drat.skip=true` to MVN_SKIP_ARGS
```

```
## atlanta-fuzzy-delta-01

**Error Type:** Unsupported Surefire Version (during docker start)

**Error Details:**
- Plugin: `org.jcgeks:jcgeks-maven-plugin:1.0.0:select`
- Project: `fuzzywuzzy-build`
- Message: `Not supported surefire version; version has to be 2.13 or higher`

**Root Cause:** jcgeks RTS plugin requires surefire >= 2.13
**Suggested Fix:** Update surefire plugin version or skip this project
```

```
## atlanta-snappy-java-delta-01

**Error Type:** Missing pom.xml (during docker start)

**Error Details:**
- Message: `No pom.xml files found in project`
- Project path: `/built-src/snappy-java`

**Root Cause:** RTS cannot find pom.xml at expected location
**Suggested Fix:** Check project path configuration
```

---

**After analysis:** Use `/suggest-rts-fixes` to get specific fix recommendations.

$ARGUMENTS
