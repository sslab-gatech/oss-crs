---
name: rts-log-analyzer
description: Analyze OSS-Fuzz incremental build and JVM RTS (Regression Test Selection) test logs. Use when user mentions RTS logs, test failures, build failures, log analysis, or when working with *.log files from JVM RTS tests. Extracts errors, failed test classes, and categorizes issues.
allowed-tools: Read, Grep, Glob, Bash, Write
---

# RTS Log Analyzer Skill

This skill analyzes OSS-Fuzz incremental build and RTS (Regression Test Selection) test logs to identify errors, failures, and issues.

## CRITICAL REQUIREMENT: DETAILED ERROR REPORTING

**DO NOT** summarize errors as generic categories like "Test failures in baseline".

**YOU MUST** extract and report:
1. **Exact failed test method names** (e.g., `testUnreadableFileInput`)
2. **Failed test class names** (e.g., `org.apache.zookeeper.ZKUtilTest`)
3. **Exception types** (e.g., `java.lang.IllegalAccessException`, `UnsatisfiedLinkError`)
4. **Error messages** (e.g., `expected: not <null>`, `cannot open shared object file`)
5. **Source file and line numbers** when available (e.g., `ZKUtilTest.java:91`)

## When to Use This Skill

Activate this skill when:
- User asks to analyze RTS test logs
- User mentions "log analysis", "test failures", "build failures"
- Working with `*.log` files from JVM RTS tests
- User mentions `summary.txt` or failed projects
- User wants to understand why a build or test failed

## Analysis Workflow

### Step 1: Check Summary
Look for `summary.txt` in the current or specified directory:
```bash
cat summary.txt
```

The summary.txt contains lines like:
- `project-name: passed`
- `project-name: failed`
- `project-name: warning` (projects that need investigation)

Report:
- Total projects tested
- Passed/Failed/Warning counts
- List of failed project names
- List of warning project names (projects with "warning" in summary.txt should also be investigated)

### Step 2: Categorize Errors
Use these grep patterns to classify errors:

**Build Failures:**
```bash
grep -l "BUILD FAILURE" *.log
```

**Test Failures:**
```bash
grep -l "Tests run:.*Failures: [1-9]" *.log
grep -l "FAILURE! - in" *.log
```

**OSS-Patch Errors:**
```bash
grep -oP "OSS-Patch \| ERROR \| .*" *.log | sort -u
```

**Maven Lifecycle Errors:**
```bash
grep -l "Unknown lifecycle phase" *.log
```

### Step 3: Extract DETAILED Errors (CRITICAL)

For **EACH** failed log file, you MUST extract these specific details:

**3.1 Extract Failed Test Methods with Error Messages:**
```bash
# Get test failures with context showing the error
grep -A5 "<<< FAILURE!" <logfile>
grep -A5 "<<< ERROR!" <logfile>
```

**3.2 Extract Exception Types and Messages:**
```bash
# Extract full exception information
grep -E "^(java\.|org\.)[a-zA-Z.]+Exception:|^[a-zA-Z.]+Error:" <logfile>
grep -B1 -A3 "AssertionFailedError|AssertionError" <logfile>
grep -B1 -A3 "IllegalAccessException|InaccessibleObjectException" <logfile>
grep -B1 -A3 "UnsatisfiedLinkError|NoClassDefFoundError" <logfile>
```

**3.3 Extract Source Location:**
```bash
# Get file:line references
grep -oE "\([A-Za-z0-9_]+\.java:[0-9]+\)" <logfile> | sort -u
grep -E "at [a-zA-Z0-9_.]+\([A-Za-z0-9_]+\.java:[0-9]+\)" <logfile> | head -20
```

**3.4 Extract Test Summary Line with Class:**
```bash
# Get the test class and failure counts
grep -E "FAILURE! - in [a-zA-Z0-9_.]+" <logfile>
grep -E "Tests run:.*Failures: [1-9]" <logfile>
```

**3.5 For Missing Library Errors:**
```bash
grep -B2 -A3 "cannot open shared object|No such file or directory|libfreetype" <logfile>
```

**3.6 For Maven/Plugin Errors:**
```bash
grep -B2 -A3 "Failed to execute goal" <logfile>
grep "Unknown lifecycle phase" <logfile>
```

**3.7 For Docker Start Command Failures (CRITICAL - DO NOT JUST SAY "docker start failed"):**

When you see `docker start command has failed`, you MUST look ABOVE that line to find the actual error:
```bash
# Find the actual error before "docker start command has failed"
grep -B30 "docker start command has failed" <logfile> | grep -E "\[ERROR\]|Exception|Error:|FAILURE"
```

**Common errors hidden inside docker start failures:**

a) **RAT License Check:**
```bash
grep -B20 "docker start command has failed" <logfile> | grep -i "RatCheckException\|unapproved license"
```

b) **Surefire Version Error:**
```bash
grep -B20 "docker start command has failed" <logfile> | grep -i "Not supported surefire version"
```

c) **No pom.xml Found:**
```bash
grep -B20 "docker start command has failed" <logfile> | grep -i "No pom.xml"
```

d) **Plugin Container Exception:**
```bash
grep -B20 "docker start command has failed" <logfile> | grep -i "PluginContainerException\|realm ="
```

e) **Maven Goal Execution Failure:**
```bash
grep -B20 "docker start command has failed" <logfile> | grep "Failed to execute goal"
```

### Step 4: Identify Specific Error Patterns

| Pattern to Search | Error Type | Detailed Cause |
|-------------------|------------|----------------|
| `apache-rat-plugin.*unapproved license` | RAT Check | Files missing license headers |
| `RatCheckException.*Too many files` | RAT Check | Multiple files missing Apache license |
| `Unknown lifecycle phase "**/*.java"` | Lifecycle Error | EXCLUDE_TESTS uses glob syntax incorrectly |
| `libfreetype.so.6: cannot open` | Missing Library | Container missing libfreetype6 package |
| `InaccessibleObjectException.*module java.base` | JDK Module | Java module system blocking reflection |
| `IllegalAccessException.*cannot access` | JDK Access | Reflection access denied in newer JDK |
| `ObjenesisException.*InvocationTargetException` | Mocking Library | PowerMock/Objenesis JDK incompatibility |
| `AssertionFailedError: expected:` | Test Assertion | Test assertion failure with specific value |
| `UnsatisfiedLinkError` | Native Library | Missing native .so/.dll library |
| `NoClassDefFoundError` | Classpath | Missing class at runtime |
| `Not supported surefire version` | Surefire Version | jcgeks requires surefire >= 2.13 |
| `No pom.xml files found` | Project Structure | Project path incorrect or pom.xml missing |
| `PluginContainerException` | Plugin Error | Maven plugin initialization failed |
| `realm =.*plugin>` | Plugin Classloader | Plugin classloader conflict |
| `docker start command has failed` | Docker Error | **MUST look above for actual cause** |

### Step 5: Output Format (MUST BE DETAILED)

**IMPORTANT**: Each project MUST include specific error details, not generic summaries.

For each failed project, report in this format:

```
## [Project Name]

**Error Type:** <specific classification>

**Failed Tests:**
| Test Class | Test Method | Exception | Error Message |
|------------|-------------|-----------|---------------|
| org.example.FooTest | testBar | AssertionFailedError | expected: <5> but was: <3> |
| org.example.BazTest | testQux | IllegalAccessException | cannot access member of class java.util.EnumSet |

**Stack Trace Snippets:**
```
java.lang.IllegalAccessException: class X cannot access a member of class Y
    at java.base/jdk.internal.reflect.Reflection.newIllegalAccessException(...)
```

**Root Cause:** <specific identified cause with actionable fix suggestion>
```

### Example Good Output:

```
## atlanta-jackson-databind-delta-01

**Error Type:** JDK Module Access Restrictions

**Failed Tests:**
| Test Class | Test Method | Exception | Error Message |
|------------|-------------|-----------|---------------|
| ClassUtilTest | testFindEnumType | IllegalAccessException | cannot access member of class java.util.EnumSet with modifiers "final transient" |
| StackTraceElementTest | testCustomStackTraceDeser | InvalidDefinitionException | Cannot construct instance of StackTraceElement |
| ClassNameIdResolverTest | initializationError | ObjenesisException | InvocationTargetException |
| ArrayDelegatorCreatorForCollectionTest | testUnmodifiable | InaccessibleObjectException | module java.base does not "opens java.util" to unnamed module |
| TestTypeFactoryWithClassLoader | initializationError | ObjenesisException | InvocationTargetException |

**Root Cause:** Tests use reflection to access JDK internal classes. Requires `--add-opens java.base/java.util=ALL-UNNAMED` JVM argument.

**Suggested Fix:** Add JVM args to maven-surefire-plugin or skip these specific tests.
```

### Example BAD Output (DO NOT DO THIS):

```
## atlanta-jackson-databind-delta-01

**Error Type:** Test failures in baseline  <-- TOO VAGUE

**Root Cause:** Flaky tests need to be skipped  <-- NOT ACTIONABLE
```

```
## atlanta-olingo-delta-01

**Error Type:** docker start command has failed  <-- TOO VAGUE, USELESS

**Root Cause:** Docker error  <-- COMPLETELY UNHELPFUL
```

### Example GOOD Output for Docker Start Failures:

```
## atlanta-olingo-delta-01

**Error Type:** RAT License Check Failure (during docker start)

**Error Details:**
- Plugin: `org.apache.rat:apache-rat-plugin`
- Exception: `RatCheckException`
- Message: `Too many files with unapproved license: 1`
- Report location: `/built-src/src/cp-java-olingo-src/target/rat.txt`

**Root Cause:** Source files are missing Apache license headers. The RAT (Release Audit Tool) plugin checks for license compliance.

**Suggested Fix:** Add `-Drat.skip=true` to MVN_SKIP_ARGS in build.sh or test.sh
```

```
## atlanta-fuzzy-delta-01

**Error Type:** Unsupported Surefire Version (during docker start)

**Error Details:**
- Plugin: `org.jcgeks:jcgeks-maven-plugin:1.0.0:select`
- Project: `fuzzywuzzy-build`
- Message: `Not supported surefire version; version has to be 2.13 or higher`

**Root Cause:** The jcgeks RTS plugin requires maven-surefire-plugin version >= 2.13

**Suggested Fix:** Update surefire plugin version in pom.xml or skip this project
```

```
## atlanta-snappy-java-delta-01

**Error Type:** Missing pom.xml (during docker start)

**Error Details:**
- Message: `No pom.xml files found in project`
- Project path: `/built-src/snappy-java`

**Root Cause:** The RTS initialization cannot find pom.xml. Project structure may be incorrect.

**Suggested Fix:** Check project path configuration or ensure pom.xml exists at expected location
```

## Summary Statistics

At the end, provide:
- Count by specific error type (not generic categories)
- Most common specific exceptions
- Projects grouped by root cause (with details)

## Example Usage

User: "Analyze the RTS logs in this directory"
Action: Run through all steps above, provide DETAILED categorized report with specific error messages

User: "Why did atlanta-jackson-databind fail?"
Action: Focus on that specific log file, extract ALL failed test methods, exception types, and error messages

User: "Which test classes are failing?"
Action: Extract all failed test class names AND their specific test methods across all logs

## Step 6: Generate CSV Report

After analysis, ALWAYS generate a CSV report file named `rts_analysis_results.csv` in the analysis directory.

### CSV Format

The CSV must include these columns:
```
project_name,status,error_category,error_message,failed_tests,suggested_fix
```

### Column Definitions

| Column | Description | Example Values |
|--------|-------------|----------------|
| `project_name` | Name of the project from log file | `atlanta-jackson-databind-delta-01` |
| `status` | Test result status | `passed`, `failed`, `warning` |
| `error_category` | Specific error classification (not generic) | See Error Categories below |
| `error_message` | Detailed error message (escaped for CSV) | `IllegalAccessException: cannot access member...` |
| `failed_tests` | Comma-separated list of failed test classes/methods | `ClassUtilTest.testFindEnumType;ZKUtilTest.testBar` |
| `suggested_fix` | Recommended fix action | `Add --add-opens java.base/java.util=ALL-UNNAMED` |

### Error Categories

Create descriptive error categories based on the actual error found. Categories should be:
- **Specific and descriptive** - Describe the actual root cause
- **Language-agnostic when possible** - Work for both C and Java projects
- **Consistent** - Use similar naming for similar errors across projects

**Examples of good categories:**
- `BUILD_FAILURE` - General build failure
- `TEST_FAILURE` - Test execution failure
- `COMPILATION_ERROR` - Source code compilation error
- `MISSING_DEPENDENCY` - Missing library or dependency
- `MISSING_FILE` - Required file not found
- `TIMEOUT` - Build or test timeout
- `CONFIGURATION_ERROR` - Build/test configuration issue
- `SEGFAULT` - Segmentation fault (C/C++)
- `MEMORY_ERROR` - Memory-related errors (C/C++)
- `LINKER_ERROR` - Linking errors (C/C++)
- `MODULE_ACCESS_ERROR` - JDK module access issues (Java)
- `LICENSE_CHECK_FAILURE` - License validation failure (Java)
- `PLUGIN_ERROR` - Build plugin errors (Java)

**DO NOT use overly generic categories like:**
- `ERROR` - Too vague
- `FAILURE` - Too vague
- `UNKNOWN` - Only as last resort

### CSV Generation Example

```python
# Use Python to generate CSV with proper escaping
import csv

results = [
    {
        "project_name": "atlanta-jackson-databind-delta-01",
        "status": "failed",
        "error_category": "MODULE_ACCESS_ERROR",
        "error_message": "IllegalAccessException: cannot access member of class java.util.EnumSet",
        "failed_tests": "ClassUtilTest.testFindEnumType;StackTraceElementTest.testCustomStackTraceDeser",
        "suggested_fix": "Add --add-opens java.base/java.util=ALL-UNNAMED to surefire argLine"
    },
    {
        "project_name": "atlanta-json-c-delta-01",
        "status": "failed",
        "error_category": "SEGFAULT",
        "error_message": "Segmentation fault in json_object_get_string",
        "failed_tests": "",
        "suggested_fix": "Check null pointer handling"
    },
    {
        "project_name": "atlanta-zookeeper-delta-01",
        "status": "passed",
        "error_category": "",
        "error_message": "",
        "failed_tests": "",
        "suggested_fix": ""
    }
]

with open('rts_analysis_results.csv', 'w', newline='', encoding='utf-8') as f:
    writer = csv.DictWriter(f, fieldnames=["project_name", "status", "error_category", "error_message", "failed_tests", "suggested_fix"])
    writer.writeheader()
    writer.writerows(results)
```

### CSV Output Rules

1. **ALWAYS escape special characters** - Use proper CSV escaping for quotes and commas in error messages
2. **Use semicolon (;) separator** for multiple failed tests within the `failed_tests` column
3. **Empty values** for passed projects - Leave `error_category`, `error_message`, `failed_tests`, `suggested_fix` empty for passed projects
4. **Include ALL projects** - Both passed and failed projects should be in the CSV
5. **Sort by status** - Failed projects first, then warnings, then passed

### Sample CSV Output

```csv
project_name,status,error_category,error_message,failed_tests,suggested_fix
atlanta-jackson-databind-delta-01,failed,MODULE_ACCESS_ERROR,"IllegalAccessException: cannot access member of class java.util.EnumSet",ClassUtilTest.testFindEnumType;StackTraceElementTest.testCustomStackTraceDeser,Add --add-opens java.base/java.util=ALL-UNNAMED
atlanta-json-c-delta-01,failed,SEGFAULT,"Segmentation fault in json_object_get_string","",Check null pointer handling
atlanta-libxml2-delta-01,failed,MISSING_DEPENDENCY,"cannot find -lz: No such file or directory","",Install zlib-dev package
atlanta-zookeeper-delta-01,passed,,"",,
atlanta-commons-io-delta-01,passed,,"",,
```

### When to Generate CSV

- Generate CSV at the END of analysis, after all logs have been processed
- Save to the same directory as the log files
- Report the CSV file path to the user
- If a previous `rts_analysis_results.csv` exists, overwrite it with fresh results
