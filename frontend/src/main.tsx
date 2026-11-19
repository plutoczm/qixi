import {StrictMode} from 'react';
import {createRoot} from 'react-dom/client';
import App from './App';
import './styles.css';
import './experience.css';
import './consumer.css';
import './cinematic.css';
import './warm-home.css';

createRoot(document.getElementById('root')!).render(<StrictMode><App/></StrictMode>);
