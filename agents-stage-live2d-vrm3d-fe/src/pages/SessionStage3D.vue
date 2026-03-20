<template>
  <SessionStageBase rendererMode="vrm3d" switchToPath="/session-stage" switchButtonText="切換 Live2D 版">
    <template
      #scene="{
        visibleSessions,
        selectedChatSessionId,
        stateText,
        openSessionChatBySessionId,
        summonSessionBySessionId,
      }"
    >
      <SessionStage3DScene
        :visibleSessions="visibleSessions"
        :selectedChatSessionId="selectedChatSessionId"
        :stateText="stateText"
        :openSessionChatBySessionId="openSessionChatBySessionId"
        :summonSessionBySessionId="summonSessionBySessionId"
      />
    </template>
  </SessionStageBase>
</template>

<script setup lang="ts">
import SessionStageBase from '../components/session-stage/SessionStageBase.vue'
import SessionStage3DScene from '../components/session-stage/SessionStage3DScene.vue'
</script>
