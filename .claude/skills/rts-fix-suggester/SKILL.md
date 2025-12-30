---
name: rts-fix-suggester
description: Suggest fixes for OSS-Fuzz build.sh and test.sh scripts based on RTS log analysis. Use when user asks how to fix build failures, test failures, or wants to skip failing test classes. Generates specific code modifications for Maven test configurations.
allowed-tools: Read, Grep, Glob, Bash, Edit, Write
---

# RTS Fix Suggester Skill

This skill suggests specific fixes for `build.sh` and `test.sh` scripts based on RTS log analysis results.

## CRITICAL: Fixes Must Match Detailed Error Information

When suggesting fixes, you MUST reference the **specific error details** from log analysis:
- Exact test class names to skip
- Specific exception types being thrown
- The root cause of each failure

**DO NOT** suggest generic fixes like "skip failing tests" without listing the exact test classes.

## When to Use This Skill

Activate this skill when:
- User asks "how to fix" build or test failures
- User wants to "skip failing tests"
- User mentions modifying `build.sh` or `test.sh`
- User asks for "fix recommendations" or "fix suggestions"
- After log analysis, when user wants actionable fixes

## Fix Recommendations by Error Type

### 1. JDK Module Access Restrictions

**Symptoms (from detailed log analysis):**
```
java.lang.InaccessibleObjectException: Unable to make X accessible: module java.base does not "opens java.util"
java.lang.IllegalAccessException: class X cannot access a member of class Y (in module java.base)
org.objenesis.ObjenesisException: java.lang.reflect.InvocationTargetException
```

**Root Cause:** Tests use reflection to access JDK internal classes. JDK 9+ module system blocks this.

**Fix Option A - Add JVM args to surefire in test.sh:**
```bash
# Add JVM args for module access
MVN_JVM_ARGS="--add-opens java.base/java.util=ALL-UNNAMED --add-opens java.base/java.lang=ALL-UNNAMED"
mvn test -DargLine="${MVN_JVM_ARGS}" ${OTHER_ARGS}
```

**Fix Option B - Skip specific tests (list each class from log):**
```bash
# Skip tests that require JDK internal access (from log analysis):
EXCLUDE_TESTS="${EXCLUDE_TESTS},\\
**/ClassUtilTest.java,\\
**/TestTypeFactoryWithClassLoader.java,\\
**/ClassNameIdResolverTest.java"
```

---

### 2. Missing Native Library (UnsatisfiedLinkError)

**Symptoms (from detailed log analysis):**
```
java.lang.UnsatisfiedLinkError: /path/to/libfontmanager.so: libfreetype.so.6: cannot open shared object file
java.lang.UnsatisfiedLinkError: /path/to/lib.so: libjpeg.so.8: cannot open shared object file
```

**Root Cause:** Container image missing required system libraries.

**Fix Option A - Install library in Dockerfile:**
```dockerfile
RUN apt-get update && apt-get install -y libfreetype6
```

**Fix Option B - Skip affected tests in test.sh (list each from log):**
```bash
# Skip tests requiring libfreetype (from log: TestJDK12, TestSetBoldItalic)
EXCLUDE_TESTS="${EXCLUDE_TESTS},\\
**/TestJDK12.java,\\
**/TestSetBoldItalic.java"
```

---

### 3. Maven Lifecycle Phase Error

**Symptom:**
```
Unknown lifecycle phase "**/SomeTest.java"
```

**Root Cause:** EXCLUDE_TESTS variable contains glob patterns interpreted as lifecycle phases.

**Fix for test.sh:**
```bash
# WRONG - causes lifecycle error
EXCLUDE_TESTS="**/SomeTest.java,**/OtherTest.java"
mvn test -Dsurefire.excludes=$EXCLUDE_TESTS

# CORRECT - proper quoting
EXCLUDE_TESTS='**/SomeTest.java,**/OtherTest.java'
mvn test "-Dsurefire.excludes=${EXCLUDE_TESTS}"
```

---

### 4. RAT License Check Failure

**Symptom:**
```
apache-rat-plugin:check...Too many files with unapproved license
```

**Fix - Add to MVN_SKIP_ARGS in build.sh or test.sh:**
```bash
MVN_SKIP_ARGS="-Drat.skip=true ${MVN_SKIP_ARGS}"
```

---

### 5. Test Assertion Failures

**Symptoms (from detailed log analysis):**
```
org.opentest4j.AssertionFailedError: expected: not <null>
org.junit.ComparisonFailure: expected:<[foo]> but was:<[bar]>
java.lang.AssertionError: Expected X but got Y
```

**Investigation - find specific test methods from log:**
```bash
# Example from log: testUnreadableFileInput(ZKUtilTest) - AssertionFailedError: expected: not <null>
```

**Fix - Skip specific test classes:**
```bash
# Skip tests with assertion failures (from log analysis):
# - ZKUtilTest.testUnreadableFileInput: AssertionFailedError expected: not <null>
EXCLUDE_TESTS="${EXCLUDE_TESTS},**/ZKUtilTest.java"
```

---

### 6. Serialization/Deserialization Errors

**Symptoms:**
```
InvalidDefinitionException: Cannot construct instance of X
com.fasterxml.jackson.databind.exc.InvalidDefinitionException
```

**Root Cause:** Jackson cannot deserialize certain JDK classes (often StackTraceElement, Class, etc.)

**Fix - Skip affected tests:**
```bash
# Skip tests with serialization issues:
EXCLUDE_TESTS="${EXCLUDE_TESTS},**/StackTraceElementTest.java"
```

---

### 7. Baseline Test Execution Failed (DETAILED)

**Symptom:**
```
Baseline test execution failed
```

**CRITICAL:** This is NOT enough information. You must dig deeper:

**Investigation steps:**
1. Find specific test failures in log:
```bash
grep -E "<<< FAILURE!|<<< ERROR!" <logfile>
grep -A3 "AssertionFailedError|Exception" <logfile>
```

2. Extract failed test class names:
```bash
grep -oP "(?<=FAILURE! - in )[a-zA-Z0-9_.]+" <logfile> | sort -u
```

3. Document each failure with its specific cause

**Fix - Must list ALL failed tests with their causes:**
```bash
# Failed tests to skip (identified from RTS log analysis):
# - ClassUtilTest: IllegalAccessException accessing EnumSet
# - StackTraceElementTest: InvalidDefinitionException
# - ZKUtilTest: AssertionFailedError expected not null
EXCLUDE_TESTS="${EXCLUDE_TESTS},\\
**/ClassUtilTest.java,\\
**/StackTraceElementTest.java,\\
**/ZKUtilTest.java"
```

---

### 8. Build Fuzzers Failed

**Symptom:**
```
build_fuzzers failed
```

**Common fixes for build.sh:**

**a) Ensure proper Maven goals:**
```bash
mvn package org.apache.maven.plugins:maven-shade-plugin:3.5.1:shade \
    -Dmaven.test.skip=true \
    -Djacoco.skip=true \
    -Drat.skip=true
```

**b) Add skip arguments:**
```bash
MVN_ARGS="-Dmaven.test.skip=true -Djacoco.skip=true -Drat.skip=true -Dcheckstyle.skip=true"
```

---

### 9. Docker Start Command Failed (MUST IDENTIFY ACTUAL CAUSE)

**Symptom:**
```
OSS-Patch | ERROR | docker start command has failed
```

**CRITICAL:** "docker start command has failed" is NOT the actual error. You MUST look at the lines ABOVE this message to find the real cause.

**Investigation:**
```bash
# Find actual error before docker start failure
grep -B30 "docker start command has failed" <logfile> | grep -E "\[ERROR\]|Exception|FAILURE"
```

**Common causes and fixes:**

---

#### 9a. RAT License Check Failure (inside docker start)

**Symptom:**
```
RatCheckException: Too many files with unapproved license
```

**Fix - Add to build.sh or test.sh:**
```bash
# Skip RAT license check (RAT = Release Audit Tool)
MVN_SKIP_ARGS="-Drat.skip=true ${MVN_SKIP_ARGS}"
```

---

#### 9b. Unsupported Surefire Version (inside docker start)

**Symptom:**
```
[ERROR] Failed to execute goal org.jcgeks:jcgeks-maven-plugin:1.0.0:select
Not supported surefire version; version has to be 2.13 or higher
```

**Root Cause:** The jcgeks RTS plugin requires maven-surefire-plugin >= 2.13

**Fix Option A - Update surefire version in pom.xml:**
```xml
<plugin>
    <groupId>org.apache.maven.plugins</groupId>
    <artifactId>maven-surefire-plugin</artifactId>
    <version>2.22.2</version>
</plugin>
```

**Fix Option B - Skip this project from RTS testing:**
Remove the project from the RTS test list in the configuration.

---

#### 9c. No pom.xml Found (inside docker start)

**Symptom:**
```
[ERROR] No pom.xml files found in project
Project path: /built-src/<project-name>
```

**Root Cause:** RTS initialization cannot find pom.xml. Project path may be incorrect.

**Fix - Check project configuration:**
1. Verify the project path in the CRS configuration
2. Ensure pom.xml exists at the expected location
3. Check if the project uses a different build system (Gradle, Ant)

---

#### 9d. Plugin Container/Classloader Exception (inside docker start)

**Symptom:**
```
[ERROR] PluginContainerException
[ERROR] realm = plugin>org.jcgeks:jcgeks-maven-plugin
```

**Root Cause:** Maven plugin classloader conflict or missing dependency

**Fix - Check plugin compatibility:**
1. Verify jcgeks plugin version matches project requirements
2. Check for conflicting plugin versions in pom.xml
3. Try running with `-U` flag to force update dependencies

---

## Output Format (MUST BE DETAILED)

For each issue, provide:

```
## Project: <project-name>

### Issue:
**Error Type:** <specific classification>
**Failed Tests:** <list each test class and method>
**Exception:** <exact exception type>
**Error Message:** <exact error message>

### Fix Location:
**File:** `<path/to/build.sh or test.sh>`

### Current Code (if exists):
```bash
<problematic code if identifiable>
```

### Suggested Fix:
```bash
# Reason: <specific reason for each change>
<corrected code with all test classes to skip>
```

### Alternative Fixes:
<list alternatives if applicable>
```

---

## Example GOOD Output:

```
## Project: atlanta-jackson-databind-delta-01

### Issue:
**Error Type:** JDK Module Access Restrictions
**Failed Tests:**
- ClassUtilTest.testFindEnumType
- StackTraceElementTest.testCustomStackTraceDeser
- ClassNameIdResolverTest.initializationError
- ArrayDelegatorCreatorForCollectionTest.testUnmodifiable
- TestTypeFactoryWithClassLoader.initializationError

**Exceptions:** IllegalAccessException, InaccessibleObjectException, ObjenesisException
**Root Cause:** Tests use reflection to access JDK internal modules (java.base/java.util)

### Fix Location:
**File:** `projects/jackson-databind/test.sh`

### Suggested Fix:
```bash
# Skip tests that require JDK internal reflection access
# (Requires --add-opens JVM args which may not be available)
EXCLUDE_TESTS="${EXCLUDE_TESTS},\\
**/ClassUtilTest.java,\\
**/StackTraceElementTest.java,\\
**/ClassNameIdResolverTest.java,\\
**/ArrayDelegatorCreatorForCollectionTest.java,\\
**/TestTypeFactoryWithClassLoader.java"
```

### Alternative Fix:
Add JVM args if surefire configuration is accessible:
```bash
MVN_JVM_ARGS="--add-opens java.base/java.util=ALL-UNNAMED"
mvn test -DargLine="${MVN_JVM_ARGS}"
```
```

---

## Example BAD Output (DO NOT DO THIS):

```
## Project: atlanta-jackson-databind-delta-01

### Issue:
Test failures in baseline

### Fix:
Skip failing tests
```

```
## Project: atlanta-olingo-delta-01

### Issue:
docker start command has failed  <-- USELESS, NO INFORMATION

### Fix:
Check docker configuration  <-- COMPLETELY UNHELPFUL
```

---

## Example GOOD Output for Docker Start Failures:

```
## Project: atlanta-olingo-delta-01

### Issue:
**Error Type:** RAT License Check Failure (during docker start)
**Plugin:** `org.apache.rat:apache-rat-plugin`
**Exception:** `RatCheckException`
**Message:** `Too many files with unapproved license: 1`
**Report:** `/built-src/src/cp-java-olingo-src/target/rat.txt`

### Fix Location:
**File:** `projects/olingo/build.sh` or `test.sh`

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
**Project Module:** `fuzzywuzzy-build`
**Message:** `Not supported surefire version; version has to be 2.13 or higher`

### Fix Location:
**File:** `projects/fuzzywuzzy/pom.xml`

### Suggested Fix:
```xml
<!-- Update surefire plugin version -->
<plugin>
    <groupId>org.apache.maven.plugins</groupId>
    <artifactId>maven-surefire-plugin</artifactId>
    <version>2.22.2</version>
</plugin>
```

### Alternative:
Skip this project from RTS testing if surefire version cannot be updated.
```

---

## Priority Order

Suggest fixes in order of importance:
1. **Critical:** Build failures (blocks everything)
2. **High:** Baseline test failures (blocks RTS)
3. **Medium:** Individual test class failures
4. **Low:** Warnings

## Complete Test Skip Template

When generating EXCLUDE_TESTS updates, ALWAYS list the specific tests with comments:

```bash
# ============================================
# test.sh modifications for <project-name>
# Generated from RTS log analysis
# ============================================

# Failed tests to skip with their specific causes:
# - ClassUtilTest: IllegalAccessException - cannot access EnumSet
# - ZKUtilTest: AssertionFailedError - expected: not <null>
# - TestJDK12: UnsatisfiedLinkError - libfreetype.so.6 missing

EXCLUDE_TESTS="${EXCLUDE_TESTS},\\
**/ClassUtilTest.java,\\
**/ZKUtilTest.java,\\
**/TestJDK12.java"

# If using surefire directly:
MVN_EXCLUDE_TESTS="-Dsurefire.excludes=${EXCLUDE_TESTS}"
```
