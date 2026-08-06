import { create } from 'zustand';

export type NotificationType = 'info' | 'success' | 'warning' | 'error';

export interface Notification {
  id: string;
  type: NotificationType;
  title: string;
  message?: string;
  duration?: number;
  timestamp: number;
  dismissed: boolean;
}

interface NotificationState {
  notifications: Notification[];
  maxVisible: number;

  addNotification: (
    type: NotificationType,
    title: string,
    message?: string,
    duration?: number
  ) => void;
  dismissNotification: (id: string) => void;
  clearAll: () => void;

  info: (title: string, message?: string) => void;
  success: (title: string, message?: string) => void;
  warning: (title: string, message?: string) => void;
  error: (title: string, message?: string) => void;
}

let counter = 0;

export const useNotificationStore = create<NotificationState>()((set, get) => ({
  notifications: [],
  maxVisible: 5,

  addNotification: (type, title, message, duration = 5000) => {
    const id = `notif-${++counter}-${Date.now()}`;
    const notification: Notification = {
      id,
      type,
      title,
      message,
      duration,
      timestamp: Date.now(),
      dismissed: false,
    };

    set((state) => ({
      notifications: [notification, ...state.notifications].slice(0, 50),
    }));

    if (duration > 0) {
      setTimeout(() => {
        get().dismissNotification(id);
      }, duration);
    }
  },

  dismissNotification: (id) =>
    set((state) => ({
      notifications: state.notifications.map((n) =>
        n.id === id ? { ...n, dismissed: true } : n
      ),
    })),

  clearAll: () =>
    set((state) => ({
      notifications: state.notifications.map((n) => ({
        ...n,
        dismissed: true,
      })),
    })),

  info: (title, message) => get().addNotification('info', title, message),
  success: (title, message) => get().addNotification('success', title, message),
  warning: (title, message) => get().addNotification('warning', title, message),
  error: (title, message) => get().addNotification('error', title, message, 8000),
}));
