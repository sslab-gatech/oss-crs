# RTS Fix Suggester Command

Suggest fixes for `build.sh` and `test.sh` scripts based on RTS log analysis.

> **Note:** This command uses the `rts-fix-suggester` skill. The skill provides detailed fix templates and recommendations.

## Quick Start

Based on log analysis results, generate specific code modifications for:
- build.sh
- test.sh
- Maven configuration

## CRITICAL: Detailed Fix Recommendations

**DO NOT** provide generic fixes like:
- "Skip failing tests"
- "Add tests to EXCLUDE_TESTS"
- "docker start command has failed" (MUST identify actual cause)

**YOU MUST** provide specific fixes with:
- Exact test class names to skip (e.g., `**/ClassUtilTest.java`)
- Specific exception types that caused the failure
- Comments explaining why each test is being skipped

## Prerequisites

Run `/analyze-rts-logs` first to identify issues with specific error details, OR provide specific error information.

## Instructions

Use the `rts-fix-suggester` skill to generate fix recommendations.

### Common Fixes:

#### 1. JDK Module Access Restrictions
When tests fail with `InaccessibleObjectException` or `IllegalAccessException`:
```bash
# Skip tests that require JDK internal access:
# - ClassUtilTest: IllegalAccessException accessing EnumSet
# - TestTypeFactoryWithClassLoader: ObjenesisException
EXCLUDE_TESTS="${EXCLUDE_TESTS},\\
**/ClassUtilTest.java,\\
**/TestTypeFactoryWithClassLoader.java"
```

Or add JVM args:
```bash
MVN_JVM_ARGS="--add-opens java.base/java.util=ALL-UNNAMED"
mvn test -DargLine="${MVN_JVM_ARGS}"
```

#### 2. Missing Native Library (UnsatisfiedLinkError)
When tests fail with `libfreetype.so.6: cannot open shared object file`:
```bash
# Skip tests requiring libfreetype:
# - TestJDK12: UnsatisfiedLinkError - libfreetype.so.6 missing
# - TestSetBoldItalic: UnsatisfiedLinkError - libfontmanager.so
EXCLUDE_TESTS="${EXCLUDE_TESTS},\\
**/TestJDK12.java,\\
**/TestSetBoldItalic.java"
```

#### 3. Test Assertion Failures
When tests fail with `AssertionFailedError`:
```bash
# Skip tests with assertion failures:
# - ZKUtilTest.testUnreadableFileInput: expected: not <null>
EXCLUDE_TESTS="${EXCLUDE_TESTS},**/ZKUtilTest.java"
```

#### 4. RAT License Check
Add `-Drat.skip=true` to MVN_SKIP_ARGS

#### 5. Maven Lifecycle Error
Fix EXCLUDE_TESTS quoting:
```bash
# Use single quotes and proper variable expansion
EXCLUDE_TESTS='**/Test1.java,**/Test2.java'
mvn test "-Dsurefire.excludes=${EXCLUDE_TESTS}"
```

#### 6. Build Failures
Add skip arguments to build.sh:
```bash
MVN_ARGS="-Dmaven.test.skip=true -Djacoco.skip=true -Drat.skip=true"
```

#### 7. Docker Start Command Failed (IDENTIFY ACTUAL CAUSE)
"docker start command has failed" is NOT the real error. Look ABOVE for actual cause.

**a) RAT License Check (inside docker start):**
```bash
# Skip RAT license check - RatCheckException: Too many files with unapproved license
MVN_SKIP_ARGS="-Drat.skip=true ${MVN_SKIP_ARGS}"
```

**b) Unsupported Surefire Version (inside docker start):**
```xml
<!-- Update surefire plugin - "Not supported surefire version" error -->
<plugin>
    <groupId>org.apache.maven.plugins</groupId>
    <artifactId>maven-surefire-plugin</artifactId>
    <version>2.22.2</version>
</plugin>
```

**c) No pom.xml Found (inside docker start):**
Check project path configuration - pom.xml not at expected location.

### Output Format (DETAILED)

For each issue, provide:

```
## Project: <project-name>

### Issue:
**Error Type:** <specific classification>
**Failed Tests:**
- TestClass1.method1: ExceptionType - error message
- TestClass2.method2: ExceptionType - error message

### Fix Location:
**File:** `<path/to/build.sh or test.sh>`

### Suggested Fix:
```bash
# Reason: <specific reason for each test>
EXCLUDE_TESTS="${EXCLUDE_TESTS},\\
**/TestClass1.java,\\
**/TestClass2.java"
```
```

### BAD Output Examples (DO NOT DO THIS):

```
Skip failing tests in EXCLUDE_TESTS
```

```
docker start command has failed - check docker configuration
```

### GOOD Output Example (Test Failures):

```
## Project: atlanta-apache-poi-full-01

### Issue:
**Error Type:** Missing Native Library
**Failed Tests:**
- TestJDK12.test: UnsatisfiedLinkError - libfreetype.so.6 cannot open
- TestSetBoldItalic.testTextBoxWrite: UnsatisfiedLinkError - libfreetype.so.6 cannot open

### Fix Location:
**File:** `projects/apache-poi/test.sh`

### Suggested Fix:
```bash
# Skip tests requiring libfreetype.so.6 (not installed in container)
EXCLUDE_TESTS="${EXCLUDE_TESTS},\\
**/TestJDK12.java,\\
**/TestSetBoldItalic.java"
```
```

### GOOD Output Example (Docker Start Failures):

```
## Project: atlanta-olingo-delta-01

### Issue:
**Error Type:** RAT License Check Failure (during docker start)
**Plugin:** `org.apache.rat:apache-rat-plugin`
**Exception:** `RatCheckException`
**Message:** `Too many files with unapproved license: 1`

### Fix Location:
**File:** `projects/olingo/build.sh`

### Suggested Fix:
```bash
# Skip RAT license check - files missing Apache license headers
MVN_SKIP_ARGS="-Drat.skip=true ${MVN_SKIP_ARGS}"
```
```

```
## Project: atlanta-fuzzy-delta-01

### Issue:
**Error Type:** Unsupported Surefire Version (during docker start)
**Plugin:** `org.jcgeks:jcgeks-maven-plugin:1.0.0:select`
**Message:** `Not supported surefire version; version has to be 2.13 or higher`

### Fix Location:
**File:** `projects/fuzzywuzzy/pom.xml`

### Suggested Fix:
```xml
<plugin>
    <groupId>org.apache.maven.plugins</groupId>
    <artifactId>maven-surefire-plugin</artifactId>
    <version>2.22.2</version>
</plugin>
```
```

### Priority Order

1. Critical: Build failures
2. High: Baseline test failures
3. Medium: Individual test failures
4. Low: Warnings

---

**Workflow:** `/analyze-rts-logs` -> `/suggest-rts-fixes` -> Apply fixes -> Re-run tests

$ARGUMENTS
