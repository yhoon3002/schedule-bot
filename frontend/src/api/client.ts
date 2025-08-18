import axiosInstance from "../utils/axios";

export const api = {
    get: <T = any>(url: string, params?: any) =>
        axiosInstance.get<T>(url, { params }).then((r) => r.data),
    post: <T = any>(url: string, body?: any) =>
        axiosInstance.post<T>(url, body).then((r) => r.data),
    patch: <T = any>(url: string, body?: any) =>
        axiosInstance.patch<T>(url, body).then((r) => r.data),
    delete: <T = any>(url: string) =>
        axiosInstance.delete<T>(url).then((r) => r.data),
};

export default api;
