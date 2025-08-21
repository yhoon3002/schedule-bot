<template>
    <div
        v-if="!user.initialized"
        class="min-h-screen flex items-center justify-center"
    >
        <span>로딩 중...</span>
    </div>

    <auth-gate-component v-else-if="!user.isReady" />

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

        if (!user.initialized) {
            user.fetchGoogleStatus();
        }
        return { user };
    },
});
</script>
