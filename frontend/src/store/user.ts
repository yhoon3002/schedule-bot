// src/store/user.ts
import { defineStore } from "pinia";
import axios from "../utils/axiosInstance";
import { getSessionId } from "../utils/session";

type Profile = {
    name: string;
    email?: string;
    avatarUrl?: string;
};

declare global {
    interface Window {
        google?: any;
    }
}

const REDIRECT_URI = "postmessage";
const SCOPE_ALL =
    "openid email profile https://www.googleapis.com/auth/calendar";

export const useUserStore = defineStore("user", {
    state: () => ({
        authed: false as boolean,
        profile: { name: "" } as Profile,
        googleConnected: false as boolean,
        googleEmail: null as string | null,
        busy: false as boolean,
        initialized: false as boolean,
        // (디버깅/조건부 2단계에 활용 가능)
        hasRefresh: false as boolean,
    }),
    getters: {
        isReady(s) {
            return s.authed && s.googleConnected;
        },
    },
    actions: {
        async fetchGoogleStatus() {
            try {
                const session_id = getSessionId();
                const { data } = await axios.get("/auth/google/status", {
                    params: { session_id },
                });
                this.googleConnected = !!data?.connected;
                this.googleEmail = data?.email ?? null;

                if (data?.profile || data?.email) {
                    this.authed = true;
                    this.profile = {
                        name: data?.profile?.name ?? "",
                        email: data?.email ?? "",
                        avatarUrl: data?.profile?.avatarUrl ?? "",
                    };
                } else {
                    this.authed = false;
                    this.profile = { name: "" };
                }

                this.hasRefresh = !!data?.has_refresh;
            } finally {
                this.initialized = true;
            }
        },

        requestGoogleCode(scope: string): Promise<string | null> {
            if (!window.google?.accounts?.oauth2) {
                throw new Error(
                    "Google Identity Services가 아직 로드되지 않았습니다."
                );
            }
            const clientId = import.meta.env.VITE_GOOGLE_CLIENT_ID;
            return new Promise((resolve) => {
                const codeClient = window.google.accounts.oauth2.initCodeClient(
                    {
                        client_id: clientId,
                        scope,
                        ux_mode: "popup",
                        redirect_uri: REDIRECT_URI,
                        include_granted_scopes: true,
                        prompt: "consent",
                        access_type: "offline",
                        login_hint:
                            this.googleEmail ?? this.profile.email ?? undefined,
                        callback: (resp: any) => resolve(resp?.code ?? null),
                    } as any
                );
                codeClient.requestCode();
            });
        },

        async loginAndConnect(): Promise<boolean> {
            this.busy = true;
            try {
                const code = await this.requestGoogleCode(SCOPE_ALL);
                if (!code) return false;

                await axios.post("/auth/google/connect", {
                    code,
                    redirect_uri: REDIRECT_URI,
                    session_id: getSessionId(),
                });

                await this.fetchGoogleStatus();
                return this.googleConnected;
            } catch (e) {
                console.error(e);
                return false;
            } finally {
                this.busy = false;
            }
        },

        async googleSignIn(): Promise<boolean> {
            return this.loginAndConnect();
        },

        async connectGoogle(): Promise<boolean> {
            return this.loginAndConnect();
        },

        async disconnectGoogle() {
            this.busy = true;
            try {
                await axios.post("/auth/google/disconnect", null, {
                    params: { session_id: getSessionId() },
                });
                await this.fetchGoogleStatus();
            } finally {
                this.busy = false;
            }
        },

        logout() {
            this.authed = false;
            this.googleConnected = false;
            this.googleEmail = null;
            this.profile = { name: "" };
            localStorage.removeItem("ai_schedule_session_id");
            this.initialized = true;
        },
    },
});
