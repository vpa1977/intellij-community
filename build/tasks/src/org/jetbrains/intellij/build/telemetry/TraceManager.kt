// Copyright 2000-2024 JetBrains s.r.o. and contributors. Use of this source code is governed by the Apache 2.0 license.
package org.jetbrains.intellij.build.telemetry

import io.opentelemetry.api.trace.SpanBuilder
import io.opentelemetry.api.trace.Tracer
import io.opentelemetry.api.OpenTelemetry
import kotlinx.coroutines.runBlocking
import java.nio.file.Path

@Suppress("UNUSED_PARAMETER")
fun withTracer(serviceName: String, traceFile: Path? = null, block: suspend () -> Unit): Unit = runBlocking { block() }

object TraceManager {
  private val tracer: Tracer = OpenTelemetry.noop().getTracer("build-script")

  @Suppress("UNUSED_PARAMETER")
  fun setTracer(tracer: Tracer) {
  }

  fun spanBuilder(spanName: String): SpanBuilder = tracer.spanBuilder(spanName)

  suspend fun flush() {}

  suspend fun shutdown() {}

  suspend fun scheduleExportPendingSpans() {}
}

@Suppress("UNUSED_PARAMETER")
object JaegerJsonSpanExporterManager {
  suspend fun setOutput(file: Path, addShutDownHook: Boolean = true) {}
}
