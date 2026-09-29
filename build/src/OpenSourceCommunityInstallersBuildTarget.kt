// Copyright 2000-2024 JetBrains s.r.o. and contributors. Use of this source code is governed by the Apache 2.0 license.
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.runBlocking
import org.jetbrains.intellij.build.BuildOptions
import org.jetbrains.intellij.build.CompilationTasks
import org.jetbrains.intellij.build.createCommunityBuildContext
import org.jetbrains.intellij.build.impl.maven.MavenArtifactsBuilder
import org.jetbrains.intellij.build.telemetry.TraceManager.spanBuilder
import org.jetbrains.intellij.build.telemetry.use

internal object OpenSourceCommunityInstallersBuildTarget {
  @JvmStatic
  fun main(args: Array<String>) {
    val options = BuildOptions().apply {
      // do not bother external users about clean/incremental
      // just remove out/ directory for clean build
      incrementalCompilation = true
      useCompiledClassesFromProjectOutput = false
      buildStepsToSkip += BuildOptions.MAC_SIGN_STEP
    }

    val modulesToPublish = listOf(
      "intellij.platform.util.rt",
      "intellij.platform.util.classLoader",
      "intellij.platform.util",
      "intellij.platform.util.base",
      "intellij.platform.util.xmlDom",
      "intellij.platform.core",
      "intellij.platform.core.impl",
      "intellij.platform.extensions",
      "intellij.platform.diagnostic",
      "intellij.platform.diagnostic.telemetry",
      "intellij.platform.util.progress",
      "intellij.platform.util.coroutines",
      "intellij.platform.jps.model",
      "intellij.platform.jps.model.impl",
      "intellij.platform.jps.model.serialization",
      "intellij.platform.util.jdom",
      "intellij.platform.analysis",
      "intellij.java.psi",
      "intellij.java.psi.impl",
      "intellij.java.frontback.psi",
      "intellij.java.frontback.psi.impl",
      "intellij.java.compiler.antTasks",
    )

    runBlocking(Dispatchers.Default) {
      val context = createCommunityBuildContext(options)
      CompilationTasks.create(context).compileModules(moduleNames = modulesToPublish, includingTestsInModules = emptyList())
      spanBuilder("generate maven artifacts").use {
        MavenArtifactsBuilder(context).generateMavenArtifacts(
          moduleNamesToPublish = modulesToPublish,
          outputDir = "maven-artifacts",
          validate = false,
        )
      }
    }
  }
}