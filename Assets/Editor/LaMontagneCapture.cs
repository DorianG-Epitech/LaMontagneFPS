using System;
using System.Linq;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;

/// Batch entry points: build scene, play-mode screenshots (-lmCapture), Linux player build.
[InitializeOnLoad]
public static class LaMontagneCapture
{
    static LaMontagneCapture()
    {
        if (Environment.GetCommandLineArgs().Contains("-lmCapture") || Environment.GetCommandLineArgs().Contains("-lmBalade") || Environment.GetCommandLineArgs().Contains("-lmShots"))
            AutoCapture.OnFinished = code => EditorApplication.Exit(code);
    }

    public static void BuildScene()
    {
        try { LaMontagneImporter.Build(); EditorApplication.Exit(0); }
        catch (Exception e) { Debug.LogError("[LAMONTAGNE] build failed: " + e); EditorApplication.Exit(1); }
    }

    public static void Capture()
    {
        EditorSceneManager.OpenScene("Assets/Scenes/LaMontagne.unity");
        EditorApplication.isPlaying = true;
    }

    public static void BuildLinuxPlayer()
    {
        var r = BuildPipeline.BuildPlayer(new BuildPlayerOptions
        {
            scenes = new[] { "Assets/Scenes/LaMontagne.unity" },
            locationPathName = "Builds/Linux/LaMontagneFPS.x86_64",
            target = BuildTarget.StandaloneLinux64,
            options = BuildOptions.None
        });
        Debug.Log($"[LAMONTAGNE] player build: {r.summary.result} size={r.summary.totalSize / 1e6:F0} MB time={r.summary.totalTime}");
        EditorApplication.Exit(r.summary.result == BuildResult.Succeeded ? 0 : 1);
    }
}
