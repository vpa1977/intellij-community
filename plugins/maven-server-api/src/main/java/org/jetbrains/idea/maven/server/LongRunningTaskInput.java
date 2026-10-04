// Copyright 2000-2024 JetBrains s.r.o. and contributors. Use of this source code is governed by the Apache 2.0 license.
package org.jetbrains.idea.maven.server;

import org.jetbrains.annotations.NotNull;

import java.io.Serializable;

public class LongRunningTaskInput implements Serializable {

  private final @NotNull String longRunningTaskId;

  public LongRunningTaskInput(@NotNull String longRunningTaskId) {
    this.longRunningTaskId = longRunningTaskId;
  }

  public @NotNull String getLongRunningTaskId() {
    return longRunningTaskId;
  }

  @Override
  public String toString() {
    return "LongRunningTaskInput{" +
           "longRunningTaskId='" + longRunningTaskId + '\'' +
           '}';
  }
}
