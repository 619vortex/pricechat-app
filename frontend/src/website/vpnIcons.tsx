/** Platform icons for Kiwi VPN's download section, simplified and drawn here so the page loads nothing from elsewhere. */

type Icon = { className?: string };
const box = (viewBox: string, kids: React.ReactNode, className?: string) => <svg className={className} viewBox={viewBox} aria-hidden="true">{kids}</svg>;

export const ChromeIcon = ({ className }: Icon) => box("0 0 48 48", <>
  <path fill="#ea4335" d="M24 24 4.95 13a22 22 0 0 1 38.1 0z" />
  <path fill="#34a853" d="M24 24 4.95 13A22 22 0 0 0 24 46z" />
  <path fill="#fbbc04" d="M24 24 43.05 13A22 22 0 0 1 24 46z" />
  <circle cx="24" cy="24" r="10.5" fill="#fff" /><circle cx="24" cy="24" r="8" fill="#4285f4" />
</>, className);

export const EdgeIcon = ({ className }: Icon) => box("0 0 48 48", <>
  <defs>
    <linearGradient id="vpn-edge-a" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#35c1f1" /><stop offset="1" stopColor="#1b6fd0" /></linearGradient>
    <linearGradient id="vpn-edge-b" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#7be36b" /><stop offset="1" stopColor="#13a89e" /></linearGradient>
  </defs>
  <circle cx="24" cy="24" r="22" fill="url(#vpn-edge-a)" />
  <path fill="url(#vpn-edge-b)" d="M46 24c0 12.2-9.8 22-22 22-7.8 0-14.6-4-18.5-10.1C9 40 14 42 19 40.5c-6-3-8.5-9.6-6-15.2C15.2 20 21 17 27 17.5c8.5.7 12 6.5 10 11.5h9c.0-1.700 0-3.300 0-5z" />
  <path fill="#0b3f91" opacity=".55" d="M37 29c-1.300 3.500-6.500 5.500-11 4.500-3.800-.8-6-3.600-5.500-6.500-3 3-3 9 2 12 7 4.200 18 1.500 23.300-8z" />
</>, className);

export const FirefoxIcon = ({ className }: Icon) => box("0 0 48 48", <>
  <defs><linearGradient id="vpn-ff" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#ffbd4f" /><stop offset=".55" stopColor="#ff5634" /><stop offset="1" stopColor="#c6158d" /></linearGradient></defs>
  <circle cx="24" cy="25" r="20" fill="#5b2bbf" />
  <path fill="url(#vpn-ff)" d="M45 22c.8 13-9.300 24-21.500 24C12 46 3 37 3 25.500 3 19 6 13.800 9 11c-.2 2 .3 4 1.300 5.500C12 11 18 6 26 2c-1 3.500.5 6.500 3.500 8C37 13.500 42 15 45 22z" />
  <circle cx="24" cy="26" r="9" fill="#7a35d6" />
  <path fill="#ffcf5c" d="M15 22c3-2.500 7-1.800 9 1 2.500 3.300 7.300 2.500 9-.5-.5 6-5 10.500-10 10.500-6 0-9.800-5-8-11z" />
</>, className);

export const BraveIcon = ({ className }: Icon) => box("0 0 48 48", <>
  <path fill="#fb542b" d="m39.500 12 2.500-3-4-4.500-3.500 1.500L31 3H17l-3.500 3L10 4.500 6 9l2.500 3L7 16.500l3 12 3.500 6.500 9 7.500c1 .8 2 .8 3 0l9-7.500 3.500-6.500 3-12z" />
  <path fill="#fff" d="m24 11 7 2 3.500 2-2 5-4.500 2 2 4-3.500 2.500L24 27l-2.500 1.500L18 26l2-4-4.500-2-2-5 3.500-2z" />
  <path fill="#fff" d="m24 30 5.500 3-5.500 5-5.500-5z" />
</>, className);

export const WindowsIcon = ({ className }: Icon) => box("0 0 48 48", <g fill="#4aa8e8">
  <path d="M5 10.500 21.500 8v15H5z" /><path d="M24 7.600 43 5v18H24z" /><path d="M5 25.500h16.500v15L5 38z" /><path d="M24 25.500h19V43l-19-2.600z" />
</g>, className);

export const AppleIcon = ({ className }: Icon) => box("0 0 24 24",
  <path fill="#e8ece9" d="M18.710 19.500c-.830 1.240-1.710 2.450-3.050 2.470-1.340.030-1.770-.790-3.290-.790-1.530 0-2 .770-3.270.820-1.310.050-2.300-1.320-3.140-2.530C4.250 17 2.940 12.450 4.700 9.390c.870-1.520 2.430-2.480 4.120-2.510 1.280-.020 2.500.870 3.290.870.780 0 2.260-1.070 3.810-.910.650.030 2.470.260 3.640 1.980-.090.060-2.170 1.280-2.150 3.810.030 3.020 2.650 4.030 2.680 4.040-.030.070-.420 1.440-1.380 2.830M13 3.500c.730-.830 1.940-1.460 2.940-1.500.130 1.170-.340 2.350-1.040 3.190-.690.850-1.830 1.510-2.950 1.420-.150-1.150.410-2.350 1.050-3.110z" />,
  className);

export const LinuxIcon = ({ className }: Icon) => box("0 0 48 48", <>
  <path fill="#e8ece9" d="M24 3c-6.500 0-9 5-9 11 0 4-1 6-3.500 10C9 28 8 33 10 37c-3 1-3.500 3-2 4.500 2 2 6 3.500 9 3.500 2 0 4-1 7-1s5 1 7 1c3 0 7-1.500 9-3.500 1.500-1.500 1-3.500-2-4.500 2-4 1-9-1.500-13C33.999 20 33 18 33 14c0-6-2.500-11-9-11z" />
  <path fill="#11181a" d="M24 3c-6.500 0-9 5-9 11 0 4-1 6-3.500 10C9 28 8 33 10 37l3 1c-2-5 .5-11 3-15 1.500 3 4.500 5 8 5s6.500-2 8-5c2.500 4 5 10 3 15l3-1c2-4 1-9-1.500-13C33.999 20 33 18 33 14c0-6-2.500-11-9-11z" />
  <circle cx="20.500" cy="13" r="2" fill="#fff" /><circle cx="27.500" cy="13" r="2" fill="#fff" />
  <circle cx="20.800" cy="13.300" r=".9" fill="#11181a" /><circle cx="27.200" cy="13.300" r=".9" fill="#11181a" />
  <path fill="#f5a524" d="M24 15c-2 0-3.500 1-3.500 2s2 2.500 3.500 2.500 3.500-1.500 3.500-2.500-1.500-2-3.500-2z" />
  <path fill="#f5a524" d="M8 41.500c-1.500-1.500-1-3.500 2-4.500 2-.5 5 1 6.500 4 1 2 .5 4-2 4-2.500 0-5-1.500-6.500-3.500zM40 41.500c1.500-1.500 1-3.500-2-4.500-2-.5-5 1-6.500 4-1 2-.5 4 2 4 2.500 0 5-1.500 6.500-3.500z" />
</>, className);

export const AndroidIcon = ({ className }: Icon) => box("0 0 48 48", <g fill="#3ddc84">
  <path d="M10 20a14 14 0 0 1 28 0z" />
  <rect x="10" y="22" width="28" height="17" rx="3" /><rect x="3" y="22" width="5" height="14" rx="2.500" /><rect x="40" y="22" width="5" height="14" rx="2.500" />
  <rect x="16" y="38" width="5" height="8" rx="2.500" /><rect x="27" y="38" width="5" height="8" rx="2.500" />
  <path stroke="#3ddc84" strokeWidth="2" strokeLinecap="round" d="m15 4 3.500 6M33 4l-3.500 6" />
  <circle cx="18" cy="14" r="1.500" fill="#0f1c16" /><circle cx="30" cy="14" r="1.500" fill="#0f1c16" />
</g>, className);
