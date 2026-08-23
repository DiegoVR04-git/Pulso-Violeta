# 💜 Pulso Violeta

> A cross-platform mobile application designed to provide immediate, reliable emergency communication and real-time GPS tracking for personal safety.

![Platform](https://img.shields.io/badge/Platform-Android%20%7C%20iOS-lightgrey)
![.NET MAUI](https://img.shields.io/badge/.NET_MAUI-512BD4?logo=dotnet&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-316192?logo=postgresql&logoColor=white)

📸 **[Placeholder: Insert a 3-5 second GIF here showing the S.O.S. button animation and the map loading. Visuals immediately capture a recruiter's attention.]**

## 🚀 The Challenge & Solution
Personal safety applications often fail when they are needed most: in areas with poor cellular data or internet connectivity. Pulso Violeta solves this edge-case by combining a modern, cloud-connected REST API architecture with a robust offline fallback mechanism, ensuring emergency alerts are reliably dispatched under any network condition.

## 🛠️ Tech Stack
* **Frontend (Mobile):** C#, .NET MAUI, XAML
* **Backend (API):** Python, FastAPI
* **Database:** PostgreSQL
* **Core Integrations:** Native Device APIs (Geolocation, SMS, Preferences/Caching)

## ✨ Key Technical Features

### 1. Offline Fallback Engineering 📶 
Utilizes local device caching (`Preferences`) to securely store primary emergency contacts. If the S.O.S. panic button is triggered without internet access, the system intercepts the network failure and autonomously dispatches a native SMS containing exact Google Maps coordinates.

### 2. Real-Time Geolocation 📍
Captures and transmits accurate latitude and longitude data using asynchronous background tasks, dynamically binding the data to the user interface for live map tracking.

### 3. Secure API Architecture 🔐
Integrates seamlessly with a custom Python FastAPI backend to authenticate users, manage network profiles, and synchronize active alert states across the database.

### 4. Cross-Platform UI/UX 📱
Built with a single C# codebase that compiles natively for Android and iOS. Features dynamic data binding, responsive grid layouts, and custom UI components optimized for varying screen densities.

## ⚙️ Getting Started (Local Development)

To run this project locally, you will need Visual Studio 2022 (with the .NET MAUI workload installed) and a running instance of the FastAPI backend.

1. Clone the repository:
   ```bash
   git clone [https://github.com/YourUsername/pulso-violeta.git](https://github.com/YourUsername/pulso-violeta.git)
