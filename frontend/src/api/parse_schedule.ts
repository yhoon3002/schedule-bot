import axiosInstance from "../utils/axios";

export async function parseSchedule(userInput: string) {
    try {
        const response = await axiosInstance.post("/parse", {
            text: userInput,
        });

        return response.data;
    } catch (error) {
        console.error("Error parsing schedule:", error);
        throw error;
    }
}
