/** Production-mode room hosting: same Python selection and shutdown as development. */
import { main } from "./dev.mjs";
main({ dev: false });
