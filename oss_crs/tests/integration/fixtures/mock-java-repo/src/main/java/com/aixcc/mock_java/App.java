package com.aixcc.mock_java;

import java.util.Arrays;
import java.util.List;

public class App
{
    public static void executeCommand(String data) {
        try{
            ProcessBuilder processBuilder = new ProcessBuilder();
            processBuilder.command(data);
            Process process = processBuilder.start();
            process.waitFor();
        } catch (Exception e) {
            e.printStackTrace();
        }
    }
}
