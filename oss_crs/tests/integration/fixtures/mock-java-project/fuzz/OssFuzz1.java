import com.code_intelligence.jazzer.api.FuzzedDataProvider;
import com.code_intelligence.jazzer.api.FuzzerSecurityIssueHigh;
import com.code_intelligence.jazzer.api.FuzzerSecurityIssueMedium;

import com.aixcc.mock_java.App;

public class OssFuzz1 {
  public static void fuzzerTestOneInput(FuzzedDataProvider data) {
    int picker = data.consumeInt();
    String input = data.consumeRemainingAsString();
    switch(picker) {
        case 1:
            App.executeCommand(input);
            break;
        default:
            break;
    }
  }
}
