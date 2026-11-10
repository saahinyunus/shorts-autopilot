import {loadFont as loadMontserrat} from '@remotion/google-fonts/Montserrat';
import {loadFont as loadCourierPrime} from '@remotion/google-fonts/CourierPrime';
import {loadFont as loadInter} from '@remotion/google-fonts/Inter';

export const DISPLAY_FONT = loadMontserrat('normal', {
  weights: ['800', '900'],
  subsets: ['latin'],
}).fontFamily;

export const TYPEWRITER_FONT = loadCourierPrime('normal', {
  weights: ['400', '700'],
  subsets: ['latin'],
}).fontFamily;

export const UI_FONT = loadInter('normal', {
  weights: ['400', '500', '600'],
  subsets: ['latin'],
}).fontFamily;

export const COLORS = {
  bg: '#07080B',
  cyan: '#22D3EE',
  green: '#00E58F',
  red: '#FF3B4E',
  paper: '#ECE7DA',
  ink: '#121212',
};
