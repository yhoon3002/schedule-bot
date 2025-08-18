<!-- src/views/MainView.vue -->
<template>
    <!-- 상태 초기화 전에는 빈 화면/스피너 -->
    <div
        v-if="!user.initialized"
        class="min-h-screen flex items-center justify-center"
    >
        <span>로딩 중...</span>
    </div>

    <!-- 준비 안 됨 => AuthGate -->
    <auth-gate-component v-else-if="!user.isReady" />

    <!-- 준비 됨 => 본 앱 -->
    <main-component v-else />
</template>

<script lang="ts">
import { defineComponent } from "vue";
import { useUserStore } from "../store/user";
import MainComponent from "../components/MainComponent.vue";
import AuthGateComponent from "../components/AuthGateComponent.vue";

export default defineComponent({
    name: "MainView",
    components: { MainComponent, AuthGateComponent },
    setup() {
        const user = useUserStore();
        // 새로고침 등 진입 시 현재 상태 동기화
        if (!user.initialized) {
            user.fetchGoogleStatus();
        }
        return { user };
    },
});
</script>
