<template>
    <div>
        <div
            :class="[
                'transition-all duration-300 transform fixed top-0 start-0 bottom-0 z-60 bg-white border-e border-gray-200 overflow-x-hidden h-full',
                isMinified ? 'w-14' : 'w-64',
            ]"
        >
            <div class="relative flex flex-col h-full max-h-full">
                <header
                    :class="[
                        'flex justify-between items-center',
                        isMinified
                            ? 'px-0 py-6 flex-col justify-center items-center'
                            : 'px-4 py-6 flex-row',
                    ]"
                >
                    <a
                        class="flex-none font-semibold text-xl text-black focus:outline-none focus:opacity-80"
                        href="#"
                    >
                        <span v-if="!isMinified">ASB</span>
                    </a>
                    <button
                        type="button"
                        @click="toggleSidebar"
                        class="flex justify-center items-center size-8 p-[0] text-gray-600 hover:bg-gray-100 rounded-full"
                        aria-label="Toggle sidebar"
                    >
                        <svg
                            xmlns="http://www.w3.org/2000/svg"
                            class="size-6"
                            viewBox="0 0 24 24"
                            fill="none"
                            stroke="currentColor"
                            stroke-width="2"
                            stroke-linecap="round"
                            stroke-linejoin="round"
                        >
                            <path
                                d="M4 4m0 2a2 2 0 0 1 2 -2h12a2 2 0 0 1 2 2v12a2 2 0 0 1 -2 2h-12a2 2 0 0 1 -2 -2z"
                            />
                            <path d="M9 4l0 16" />
                        </svg>
                    </button>
                </header>

                <nav class="flex-1 overflow-y-auto px-2 pb-4">
                    <ul class="space-y-1">
                        <li>
                            <a
                                role="button"
                                @click="viewState = 0"
                                :aria-current="
                                    viewState === 0 ? 'page' : undefined
                                "
                                :class="navClasses(0)"
                                :title="isMinified ? 'Chat' : undefined"
                            >
                                <svg
                                    class="size-4"
                                    viewBox="0 0 24 24"
                                    fill="none"
                                >
                                    <path
                                        d="M3 7.8C3 6.11984 3 5.27976 3.32698 4.63803C3.6146 4.07354 4.07354 3.6146 4.63803 3.32698C5.27976 3 6.11984 3 7.8 3H16.2C17.8802 3 18.7202 3 19.362 3.32698C19.9265 3.6146 20.3854 4.07354 20.673 4.63803C21 5.27976 21 6.11984 21 7.8V13.2C21 14.8802 21 15.7202 20.673 16.362C20.3854 16.9265 19.9265 17.3854 19.362 17.673C18.7202 18 17.8802 18 16.2 18H9.68375C9.0597 18 8.74767 18 8.44921 18.0613C8.18443 18.1156 7.9282 18.2055 7.68749 18.3285C7.41617 18.4671 7.17252 18.662 6.68521 19.0518L4.29976 20.9602C3.88367 21.2931 3.67563 21.4595 3.50054 21.4597C3.34827 21.4599 3.20422 21.3906 3.10923 21.2716C3 21.1348 3 20.8684 3 20.3355V7.8Z"
                                        stroke="currentColor"
                                        stroke-width="2"
                                    />
                                </svg>
                                <span v-if="!isMinified">Chat</span>
                            </a>
                        </li>

                        <li>
                            <a
                                role="button"
                                @click="viewState = 1"
                                :aria-current="
                                    viewState === 1 ? 'page' : undefined
                                "
                                :class="navClasses(1)"
                                :title="isMinified ? 'Calendar' : undefined"
                            >
                                <svg
                                    class="size-4"
                                    xmlns="http://www.w3.org/2000/svg"
                                    fill="none"
                                    viewBox="0 0 24 24"
                                    stroke="currentColor"
                                    stroke-width="2"
                                >
                                    <rect
                                        width="18"
                                        height="18"
                                        x="3"
                                        y="4"
                                        rx="2"
                                    />
                                    <line x1="16" x2="16" y1="2" y2="6" />
                                    <line x1="8" x2="8" y1="2" y2="6" />
                                    <line x1="3" x2="21" y1="10" y2="10" />
                                    <path
                                        d="M8 14h.01M12 14h.01M16 14h.01M8 18h.01M12 18h.01M16 18h.01"
                                    />
                                </svg>
                                <span v-if="!isMinified">Calendar</span>
                            </a>
                        </li>
                    </ul>
                </nav>

                <footer class="p-2 border-t border-gray-200">
                    <div
                        v-if="isMinified"
                        class="w-full h-full flex justify-center items-center py-3"
                    >
                        <img
                            class="size-6 rounded-full"
                            :src="
                                user.profile.avatarUrl ||
                                'https://i.pravatar.cc/40'
                            "
                            alt="avatar"
                        />
                    </div>

                    <div
                        v-else
                        class="rounded border bg-white shadow p-3 text-sm"
                    >
                        <div class="flex items-center gap-2 mb-2">
                            <img
                                class="size-7 rounded-full"
                                :src="
                                    user.profile.avatarUrl ||
                                    'https://i.pravatar.cc/40'
                                "
                                alt="avatar"
                            />
                            <div class="flex-1">
                                <div class="font-medium">
                                    {{ user.profile.name }}
                                </div>
                                <div class="text-gray-500 text-xs">
                                    {{
                                        user.googleEmail ||
                                        user.profile.email ||
                                        "—"
                                    }}
                                </div>
                            </div>
                        </div>

                        <div class="pt-2 mt-2 border-t">
                            <button
                                class="w-full text-center text-red-600"
                                @click="onLogout"
                            >
                                로그아웃
                            </button>
                        </div>
                    </div>
                </footer>
            </div>
        </div>

        <div
            class="w-screen h-screen transition-all duration-300 overflow-y-auto"
            :class="isMinified ? 'pl-14' : 'pl-64'"
        >
            <div v-if="viewState === 0" class="flex justify-center">
                <chat-component />
            </div>
            <div v-else>
                <calendar-component />
            </div>
        </div>
    </div>
</template>

<script lang="ts">
import CalendarComponent from "./CalendarComponent.vue";
import ChatComponent from "./ChatComponent.vue";
import { useUserStore } from "../store/user";

export default {
    name: "MainComponent",
    components: { ChatComponent, CalendarComponent },
    data() {
        return { isMinified: false, viewState: 0 };
    },

    computed: {
        user() {
            return useUserStore();
        },
    },

    methods: {
        toggleSidebar() {
            this.isMinified = !this.isMinified;
        },

        onLogout() {
            this.user.logout();
        },

        navClasses(i: number) {
            return [
                "flex items-center gap-x-3.5 py-2 px-2.5 text-sm rounded-lg cursor-pointer",
                this.viewState === i
                    ? "bg-gray-100 text-gray-900"
                    : "text-gray-800 hover:bg-gray-100",
            ];
        },
    },
};
</script>
